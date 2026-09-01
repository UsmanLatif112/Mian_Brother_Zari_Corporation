"""Sale returns — full/partial, cash / credit / mixed refund."""

from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func

from app.extensions import db
from app.models import Sale, SaleReturn, SaleReturnItem
from app.services.audit_service import log_audit
from app.services.cashbook_service import record_cash_movement
from app.services.fifo_service import fifo_receive
from app.services.ledger_service import post_ledger_entry, rebuild_party_balances
from app.services.sync_service import enqueue_sync
from app.utils.working_date import get_working_date


def _d(value) -> Decimal:
    return Decimal(str(value or 0))


def _money(value) -> Decimal:
    return _d(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def generate_return_no() -> str:
    last = SaleReturn.query.order_by(SaleReturn.id.desc()).first()
    num = (last.id + 1) if last else 1
    return f"SR-{num:06d}"


def returned_qty_for_item(sale_item_id: int) -> Decimal:
    total = (
        db.session.query(func.coalesce(func.sum(SaleReturnItem.quantity), 0))
        .filter(SaleReturnItem.sale_item_id == sale_item_id)
        .scalar()
    )
    return _d(total)


def returned_weight_for_item(sale_item_id: int) -> Decimal:
    total = (
        db.session.query(func.coalesce(func.sum(SaleReturnItem.sale_weight), 0))
        .filter(
            SaleReturnItem.sale_item_id == sale_item_id,
            SaleReturnItem.sale_weight.isnot(None),
        )
        .scalar()
    )
    return _d(total)


def refunded_cash_for_sale(sale_id: int) -> Decimal:
    total = (
        db.session.query(func.coalesce(func.sum(SaleReturn.refund_cash), 0))
        .filter(SaleReturn.sale_id == sale_id)
        .scalar()
    )
    return _d(total)


def sale_has_returns(sale_id: int) -> bool:
    return db.session.query(SaleReturn.id).filter_by(sale_id=sale_id).first() is not None


def _line_is_open(original) -> bool:
    sold_qty = _d(original.quantity)
    sold_w = _d(original.sale_weight) if original.sale_weight is not None else None
    uw = (
        _d(original.product.unit_weight)
        if original.product and original.product.unit_weight
        else Decimal("0")
    )
    if sold_w is not None and sold_w > 0 and uw > 0:
        whole = sold_qty == sold_qty.to_integral_value()
        matches_full = abs(sold_w - sold_qty * uw) <= Decimal("0.02")
        return not (whole and matches_full)
    return bool(sold_w is not None and sold_w > 0)


def get_returnable_sale(sale_id: int) -> dict:
    """JSON payload for return modal: lines with remaining qty/weight."""
    from app.services.dashboard_service import _ensure_sale_return_schema

    _ensure_sale_return_schema()

    sale = db.session.get(Sale, sale_id)
    if not sale:
        raise ValueError("Sale not found.")

    already_cash = refunded_cash_for_sale(sale.id)
    cash_available = max(_d(sale.amount_paid) - already_cash, Decimal("0"))

    lines = []
    for it in sale.items:
        sold_qty = _d(it.quantity)
        ret_qty = returned_qty_for_item(it.id)
        rem_qty = sold_qty - ret_qty
        sold_w = _d(it.sale_weight) if it.sale_weight is not None else None
        ret_w = returned_weight_for_item(it.id) if sold_w is not None else Decimal("0")
        rem_w = (sold_w - ret_w) if sold_w is not None else None
        if rem_qty <= 0 and (rem_w is None or rem_w <= 0):
            continue

        line_total = _d(it.line_total)
        if sold_w is not None and sold_w > 0 and rem_w is not None:
            rem_value = line_total * (rem_w / sold_w)
        elif sold_qty > 0:
            rem_value = line_total * (rem_qty / sold_qty)
        else:
            rem_value = Decimal("0")

        uw = _d(it.product.unit_weight) if it.product and it.product.unit_weight else Decimal("0")
        lines.append(
            {
                "sale_item_id": it.id,
                "product_id": it.product_id,
                "product_name": it.product.name if it.product else "Item",
                "quantity_sold": float(sold_qty),
                "quantity_returned": float(ret_qty),
                "quantity_remaining": float(max(rem_qty, Decimal("0"))),
                "sale_weight": float(sold_w) if sold_w is not None else None,
                "weight_returned": float(ret_w) if sold_w is not None else None,
                "weight_remaining": float(max(rem_w, Decimal("0"))) if rem_w is not None else None,
                "weight_unit": it.weight_unit
                or (it.product.weight_unit if it.product else None)
                or "kg",
                "unit_weight": float(uw) if uw > 0 else None,
                "unit_price": float(_d(it.unit_price)),
                "line_total": float(line_total),
                "remaining_value": float(_money(rem_value)),
                "is_open": _line_is_open(it),
            }
        )

    return {
        "sale_id": sale.id,
        "invoice_no": sale.invoice_no,
        "sale_date": sale.sale_date.isoformat() if sale.sale_date else None,
        "customer_id": sale.customer_id,
        "customer_name": sale.customer.name if sale.customer else "Walk-in",
        "payment_status": sale.payment_status.value if sale.payment_status else "paid",
        "grand_total": float(_d(sale.grand_total)),
        "amount_paid": float(_d(sale.amount_paid)),
        "cash_refundable": float(cash_available),
        "items": lines,
    }


def create_sale_return(sale_id, data, user_id) -> SaleReturn:
    """
    Create a sale return.

    data:
      return_date, notes,
      refund_cash, refund_credit (optional — if omitted, all credit),
      items: [{ sale_item_id, quantity?, sale_weight? }]
    """
    from datetime import date as date_cls

    from app.services.dashboard_service import _ensure_sale_return_schema

    _ensure_sale_return_schema()

    sale = db.session.get(Sale, sale_id)
    if not sale:
        raise ValueError("Sale not found.")

    return_date = data.get("return_date") or get_working_date()
    if isinstance(return_date, str):
        return_date = date_cls.fromisoformat(return_date)

    raw_items = data.get("items") or []
    if not raw_items:
        raise ValueError("Add at least one item to return.")

    sale_items = {it.id: it for it in sale.items}
    built = []
    subtotal = Decimal("0")

    for line in raw_items:
        sid = int(line.get("sale_item_id") or 0)
        original = sale_items.get(sid)
        if not original:
            raise ValueError("Invalid sale line for return.")

        sold_qty = _d(original.quantity)
        already_qty = returned_qty_for_item(sid)
        rem_qty = sold_qty - already_qty
        if rem_qty <= 0:
            raise ValueError(
                f"Nothing left to return for "
                f"{original.product.name if original.product else 'item'}."
            )

        sold_w = _d(original.sale_weight) if original.sale_weight is not None else None
        already_w = returned_weight_for_item(sid) if sold_w is not None else Decimal("0")
        rem_w = (sold_w - already_w) if sold_w is not None else None

        ret_w = line.get("sale_weight")
        ret_qty = line.get("quantity")
        is_open = _line_is_open(original)

        if is_open and rem_w is not None and rem_w > 0 and ret_w not in (None, ""):
            ret_w = _d(ret_w)
            if ret_w <= 0:
                continue
            if ret_w > rem_w + Decimal("0.001"):
                ret_w = rem_w
            if ret_w <= 0:
                continue
            ratio = ret_w / sold_w if sold_w else Decimal("0")
            ret_qty = (sold_qty * ratio).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        else:
            ret_qty = _d(ret_qty or 0)
            if ret_qty <= 0:
                continue
            if ret_qty > rem_qty + Decimal("0.000001"):
                ret_qty = rem_qty
            if ret_qty <= 0:
                continue
            ratio = ret_qty / sold_qty if sold_qty else Decimal("0")
            ret_w = (
                (sold_w * ratio).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
                if sold_w is not None
                else None
            )

        line_total = _money(_d(original.line_total) * ratio)
        cogs = _money(_d(original.cost_of_goods) * ratio)
        if ret_qty <= 0 and (ret_w is None or ret_w <= 0):
            continue

        built.append(
            {
                "sale_item": original,
                "quantity": ret_qty,
                "sale_weight": ret_w,
                "line_total": line_total,
                "cost_of_goods": cogs,
                "unit_price": _d(original.unit_price),
                "weight_unit": original.weight_unit,
            }
        )
        subtotal += line_total

    if not built:
        raise ValueError("Add at least one item quantity/weight to return.")

    sale_sub = _d(sale.subtotal)
    sale_disc = _d(sale.discount)
    if sale_sub > 0 and sale_disc > 0:
        disc_share = _money(sale_disc * (subtotal / sale_sub))
        grand = _money(subtotal - disc_share)
    else:
        grand = _money(subtotal)

    if grand < 0:
        grand = Decimal("0")

    refund_cash = data.get("refund_cash")
    refund_credit = data.get("refund_credit")
    cash_left = max(_d(sale.amount_paid) - refunded_cash_for_sale(sale.id), Decimal("0"))

    # Paid / partial / unpaid defaults when client omits split
    if refund_cash in (None, "") and refund_credit in (None, ""):
        status = sale.payment_status.value if sale.payment_status else "paid"
        if status == "unpaid" or cash_left <= 0:
            refund_cash = Decimal("0")
            refund_credit = grand
        else:
            # Paid or partial: refund cash first up to what was collected
            refund_cash = _money(min(grand, cash_left))
            refund_credit = _money(grand - refund_cash)
    else:
        refund_cash = _money(refund_cash or 0)
        refund_credit = _money(refund_credit or 0)

    if refund_cash < 0 or refund_credit < 0:
        raise ValueError("Refund amounts cannot be negative.")

    split = _money(refund_cash + refund_credit)
    if abs(split - grand) > Decimal("0.02"):
        if abs(split - grand) <= Decimal("0.05"):
            refund_credit = _money(grand - refund_cash)
        else:
            raise ValueError(
                f"Cash + credit refund ({split}) must equal return total ({grand})."
            )

    if refund_cash > cash_left + Decimal("0.01"):
        raise ValueError(
            f"Cash refund cannot exceed cash still refundable on this sale ({cash_left})."
        )

    # Credit refund needs a customer account; walk-in paid returns must be cash
    if refund_credit > 0 and not sale.customer_id:
        raise ValueError(
            "Account credit refund requires a customer. Use cash refund for walk-in sales."
        )

    ret = SaleReturn(
        return_no=generate_return_no(),
        return_date=return_date,
        sale_id=sale.id,
        customer_id=sale.customer_id,
        salesman_id=sale.salesman_id,
        subtotal=subtotal,
        grand_total=grand,
        refund_cash=refund_cash,
        refund_credit=refund_credit,
        notes=data.get("notes"),
        created_by_id=user_id,
    )
    db.session.add(ret)
    db.session.flush()

    for row in built:
        original = row["sale_item"]
        product = original.product
        qty = row["quantity"]
        cogs = row["cost_of_goods"]
        unit_cost = (cogs / qty) if qty else Decimal("0")

        fifo_receive(
            product,
            qty,
            unit_cost,
            "sale_return",
            ret.id,
            user_id,
            notes=f"Sale return {ret.return_no} / {sale.invoice_no}",
            sale_price=original.unit_price,
            entry_at=return_date,
            unit_weight=product.unit_weight if product else None,
            weight_unit=product.weight_unit if product else None,
        )

        db.session.add(
            SaleReturnItem(
                sale_return_id=ret.id,
                sale_item_id=original.id,
                product_id=original.product_id,
                quantity=qty,
                sale_weight=row["sale_weight"],
                weight_unit=row["weight_unit"],
                unit_price=row["unit_price"],
                line_total=row["line_total"],
                cost_of_goods=cogs,
            )
        )

    if refund_cash > 0:
        record_cash_movement(
            "out",
            "sale_return_refund",
            refund_cash,
            "sale_return",
            ret.id,
            notes=f"Refund {ret.return_no} / {sale.invoice_no}",
            created_by_id=user_id,
            entry_date=return_date,
        )

    if sale.customer_id:
        # Sale: debit=invoice, credit=cash paid → balance ↑ due
        # Return: credit=return total (clears due / creates advance), debit=cash refunded
        #   unpaid return (cash=0): balance ↓ by grand
        #   paid cash return (cash=grand): balance unchanged (money goes Out in cashbook)
        #   partial: balance ↓ by credit portion only
        from app.services.sale_service import _sale_return_particulars_notes

        ret_notes = _sale_return_particulars_notes(ret, sale)
        post_ledger_entry(
            "customer",
            sale.customer_id,
            "sale_return",
            debit=refund_cash,
            credit=grand,
            entry_date=return_date,
            reference_type="sale_return",
            reference_id=ret.id,
            notes=ret_notes,
        )
        rebuild_party_balances("customer", sale.customer_id)

    if sale.salesman_id:
        from app.services.sale_service import _sale_return_particulars_notes

        post_ledger_entry(
            "salesman",
            sale.salesman_id,
            "sale_return",
            debit=refund_cash,
            credit=grand,
            entry_date=return_date,
            reference_type="sale_return",
            reference_id=ret.id,
            notes=_sale_return_particulars_notes(ret, sale),
        )
        rebuild_party_balances("salesman", sale.salesman_id)

    log_audit("create", "sale_return", ret.id, ret.return_no)
    enqueue_sync("sale_returns", ret.id, "create")
    return ret

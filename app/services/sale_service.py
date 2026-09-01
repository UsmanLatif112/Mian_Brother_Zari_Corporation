from decimal import Decimal

from app.extensions import db
from app.models import Sale, SaleItem
from app.models.sales import PaymentMethod, PaymentStatus
from app.services.audit_service import log_audit
from app.services.cashbook_service import record_cash_movement, reverse_cash_by_reference
from app.services.fifo_service import fifo_deduct, fifo_deduct_open_weight, fifo_receive
from app.services.inventory_loss_service import create_sale_backorder
from app.services.ledger_service import delete_ledger_by_reference, post_ledger_entry, rebuild_party_balances
from app.services.sync_service import enqueue_sync
from app.utils.working_date import get_working_date


def _sale_particulars_notes(sale) -> str:
    """Ledger notes: Sale INV-000021 / product particulars."""
    inv = (getattr(sale, "invoice_no", None) or "").strip() or "—"
    prefix = f"Sale {inv}"
    try:
        from app.services.journal_service import items_particulars

        text = (items_particulars(sale.items or [], fallback="", kind="sale") or "").strip()
        if text:
            return f"{prefix} / {text}"
    except Exception:
        pass
    return prefix


def _sale_return_particulars_notes(ret, sale=None) -> str:
    """Ledger notes: Return RET-… (INV-…) / product particulars."""
    sale = sale or getattr(ret, "sale", None)
    inv = (getattr(sale, "invoice_no", None) or "").strip() if sale else ""
    rno = (getattr(ret, "return_no", None) or "").strip() or "—"
    if inv:
        prefix = f"Return {rno} ({inv})"
    else:
        prefix = f"Return {rno}"
    try:
        from app.services.journal_service import items_particulars

        text = (items_particulars(ret.items or [], fallback="", kind="sale") or "").strip()
        if text:
            return f"{prefix} / {text}"
    except Exception:
        pass
    return prefix


def _post_salesman_sale_ledger(sale, grand, applied):
    """Attribute one sale row on the salesman ledger (paid / partial / unpaid)."""
    if not sale.salesman_id:
        return
    from app.models import Salesman

    salesman = db.session.get(Salesman, sale.salesman_id)
    if not salesman or salesman.is_deleted:
        return
    notes = _sale_particulars_notes(sale)
    post_ledger_entry(
        "salesman",
        salesman.id,
        "sale",
        debit=grand,
        credit=applied,
        entry_date=sale.sale_date,
        reference_type="sale",
        reference_id=sale.id,
        notes=notes,
    )
    rebuild_party_balances("salesman", salesman.id)


def generate_invoice_no():
    last = Sale.query.order_by(Sale.id.desc()).first()
    num = (last.id + 1) if last else 1
    return f"INV-{num:06d}"


def create_sale(data, items, user_id):
    """Create sale and update customer credit/advance from payment vs total."""
    from datetime import date as date_cls

    cash_received = Decimal(str(data.get("amount_paid", 0)))
    sale_date = data.get("sale_date") or get_working_date()
    if isinstance(sale_date, str):
        sale_date = date_cls.fromisoformat(sale_date)
    sale = Sale(
        invoice_no=data.get("invoice_no") or generate_invoice_no(),
        sale_date=sale_date,
        customer_id=data.get("customer_id"),
        salesman_id=data.get("salesman_id"),
        discount=Decimal(str(data.get("discount", 0))),
        tax_amount=Decimal(str(data.get("tax_amount", 0))),
        payment_method=PaymentMethod(data.get("payment_method", "cash")),
        notes=data.get("notes"),
        created_by_id=user_id,
    )
    subtotal = Decimal("0")
    db.session.add(sale)
    db.session.flush()

    for line in items:
        product = line["product"]
        qty = Decimal(str(line["quantity"]))
        unit_price = Decimal(str(line["unit_price"]))
        line_discount = Decimal(str(line.get("discount", 0)))
        if line.get("line_total") is not None:
            line_total = Decimal(str(line["line_total"]))
        else:
            line_total = qty * unit_price - line_discount
        list_unit_price = line.get("list_unit_price")
        if list_unit_price is not None:
            list_unit_price = Decimal(str(list_unit_price))
        else:
            list_unit_price = Decimal(str(product.sale_price or 0))
        sale_weight = line.get("sale_weight")
        if sale_weight is not None:
            sale_weight = Decimal(str(sale_weight))
        weight_unit = line.get("weight_unit")
        unit_weight = line.get("unit_weight")
        if unit_weight is not None and str(unit_weight).strip() != "":
            unit_weight = Decimal(str(unit_weight))
        else:
            unit_weight = None
        allow_neg = bool(line.get("allow_negative_stock") or data.get("allow_negative_stock"))
        backorder_qty = Decimal("0")
        backorder_weight = None
        if (line.get("sale_mode") or "").strip().lower() == "open" and sale_weight and sale_weight > 0:
            cogs, qty, backorder_qty, backorder_weight = fifo_deduct_open_weight(
                product,
                sale_weight,
                "sale_out",
                "sale",
                sale.id,
                user_id,
                entry_at=sale_date,
                unit_weight=unit_weight,
                allow_negative=allow_neg,
            )
            if qty > 0:
                unit_price = line_total / qty
        else:
            cogs, backorder_qty = fifo_deduct(
                product,
                qty,
                "sale_out",
                "sale",
                sale.id,
                user_id,
                entry_at=sale_date,
                unit_weight=unit_weight,
                allow_negative=allow_neg,
            )
        item = SaleItem(
            sale_id=sale.id,
            product_id=product.id,
            quantity=qty,
            unit_price=unit_price,
            list_unit_price=list_unit_price,
            discount=line_discount,
            tax_rate=Decimal(str(line.get("tax_rate", 0))),
            line_total=line_total,
            cost_of_goods=cogs,
            sale_weight=sale_weight,
            weight_unit=weight_unit,
        )
        db.session.add(item)
        db.session.flush()
        if backorder_qty and Decimal(str(backorder_qty)) > 0:
            create_sale_backorder(
                sale_item_id=item.id,
                product_id=product.id,
                qty_backordered=backorder_qty,
                weight_backordered=backorder_weight,
            )
            # Re-sync so stock includes this backorder row
            from app.services.fifo_service import sync_product_stock

            sync_product_stock(product)
        subtotal += line_total

    sale.subtotal = subtotal
    discount = Decimal(str(data.get("discount", 0) or 0))
    if discount < 0:
        discount = Decimal("0")
    if discount > subtotal:
        discount = subtotal
    sale.discount = discount
    sale.grand_total = subtotal - sale.discount + sale.tax_amount
    grand = sale.grand_total

    applied = min(cash_received, grand) if cash_received > 0 else Decimal("0")
    due = grand - applied
    excess = cash_received - applied if cash_received > applied else Decimal("0")

    # Store full cash received (Total Paid). Applied vs advance is handled in ledger.
    sale.amount_paid = cash_received
    if due <= 0:
        sale.payment_status = PaymentStatus.PAID
        sale.payment_method = PaymentMethod.CASH if cash_received > 0 else PaymentMethod.CREDIT
    elif applied > 0:
        sale.payment_status = PaymentStatus.PARTIAL
        sale.payment_method = PaymentMethod.CASH
    else:
        sale.payment_status = PaymentStatus.UNPAID
        sale.payment_method = PaymentMethod.CREDIT

    if cash_received > 0:
        record_cash_movement(
            "in",
            "sales_collection",
            cash_received,
            "sale",
            sale.id,
            created_by_id=user_id,
            entry_date=sale.sale_date,
        )

    if sale.customer_id:
        from app.models import Customer

        customer = db.session.get(Customer, sale.customer_id)
        # Always record the sale on customer ledger (paid = debit + credit equal → no balance change)
        post_ledger_entry(
            "customer",
            customer.id,
            "sale",
            debit=grand,
            credit=applied,
            entry_date=sale.sale_date,
            reference_type="sale",
            reference_id=sale.id,
            notes=_sale_particulars_notes(sale),
        )
        if due > 0:
            customer.balance = Decimal(str(customer.balance or 0)) + due
        if excess > 0:
            customer.balance = Decimal(str(customer.balance or 0)) - excess
            post_ledger_entry(
                "customer",
                customer.id,
                "sale_advance",
                debit=Decimal("0"),
                credit=excess,
                entry_date=sale.sale_date,
                reference_type="sale",
                reference_id=sale.id,
                notes=f"Overpayment advance from sale {sale.invoice_no}",
            )

    _post_salesman_sale_ledger(sale, grand, applied)

    enqueue_sync("sales", sale.id, "create")
    return sale


def update_sale(sale_id, sale_date=None, notes=None, amount_paid=None, user_id=None):
    """Update sale date/notes/payment. Line items: delete sale and recreate if needed."""
    from datetime import date as date_cls

    from app.models import CashBookEntry, Customer

    sale = db.session.get(Sale, sale_id)
    if not sale:
        raise ValueError("Sale not found.")

    if sale_date is not None:
        if isinstance(sale_date, str):
            sale.sale_date = date_cls.fromisoformat(sale_date)
        else:
            sale.sale_date = sale_date
    if notes is not None:
        sale.notes = (notes or "").strip() or None

    if amount_paid is not None:
        new_paid = Decimal(str(amount_paid or 0))
        if new_paid < 0:
            raise ValueError("Paid amount cannot be negative.")
        grand = Decimal(str(sale.grand_total or 0))
        old_cash = Decimal(str(sale.amount_paid or 0))

        cash_rows = CashBookEntry.query.filter_by(reference_type="sale", reference_id=sale.id).all()
        if cash_rows:
            old_cash = sum(
                (Decimal(str(r.amount)) for r in cash_rows if (r.entry_type or "").lower() == "in"),
                Decimal("0"),
            )

        old_applied = min(old_cash, grand)
        old_due = grand - old_applied
        old_excess = old_cash - old_applied if old_cash > old_applied else Decimal("0")

        new_applied = min(new_paid, grand)
        new_due = grand - new_applied
        new_excess = new_paid - new_applied if new_paid > new_applied else Decimal("0")

        if sale.customer_id:
            customer = db.session.get(Customer, sale.customer_id)
            if customer:
                bal = Decimal(str(customer.balance or 0))
                bal = bal - old_due + old_excess
                bal = bal + new_due - new_excess
                customer.balance = bal

        delete_ledger_by_reference("sale", sale.id, rebuild=False)
        if sale.customer_id:
            post_ledger_entry(
                "customer",
                sale.customer_id,
                "sale",
                debit=grand,
                credit=new_applied,
                entry_date=sale.sale_date,
                reference_type="sale",
                reference_id=sale.id,
                notes=_sale_particulars_notes(sale),
            )
            if new_excess > 0:
                post_ledger_entry(
                    "customer",
                    sale.customer_id,
                    "sale_advance",
                    debit=Decimal("0"),
                    credit=new_excess,
                    entry_date=sale.sale_date,
                    reference_type="sale",
                    reference_id=sale.id,
                    notes=f"Overpayment advance from sale {sale.invoice_no}",
                )
            rebuild_party_balances("customer", sale.customer_id)

        # Refresh salesman attribution for this sale (paid / partial / unpaid)
        sid = sale.salesman_id
        if sid:
            # delete_ledger_by_reference already removed old sale rows for all parties
            _post_salesman_sale_ledger(sale, grand, new_applied)

        reverse_cash_by_reference(
            "sale", sale.id, notes=f"Adjust sale {sale.invoice_no}", created_by_id=user_id
        )
        if new_paid > 0:
            record_cash_movement(
                "in",
                "sales_collection",
                new_paid,
                "sale",
                sale.id,
                created_by_id=user_id,
                entry_date=sale.sale_date,
            )

        sale.amount_paid = new_paid
        if new_due <= 0:
            sale.payment_status = PaymentStatus.PAID
            sale.payment_method = PaymentMethod.CASH if new_paid > 0 else PaymentMethod.CREDIT
        elif new_applied > 0:
            sale.payment_status = PaymentStatus.PARTIAL
            sale.payment_method = PaymentMethod.CASH
        else:
            sale.payment_status = PaymentStatus.UNPAID
            sale.payment_method = PaymentMethod.CREDIT

    log_audit("update", "sale", sale.id, sale.invoice_no)
    enqueue_sync("sales", sale.id, "update")
    return sale


def void_sale(sale_id, user_id=None):
    """Delete sale and reverse stock, cash, and ledger (customer balance rebuilt from ledger)."""
    sale = db.session.get(Sale, sale_id)
    if not sale:
        raise ValueError("Sale not found.")

    from app.services.sale_return_service import sale_has_returns

    if sale_has_returns(sale_id):
        raise ValueError(
            "This sale has returns. Delete or reverse those returns before voiding the sale."
        )

    for item in list(sale.items):
        product = item.product
        qty = Decimal(str(item.quantity or 0))
        if qty <= 0:
            continue

        from app.models import SaleItemBackorder
        from app.services.fifo_service import sync_product_stock

        open_bo = Decimal("0")
        bo = SaleItemBackorder.query.filter_by(sale_item_id=item.id).first()
        if bo:
            open_bo = Decimal(str(bo.qty_backordered or 0)) - Decimal(str(bo.qty_fulfilled or 0))
            if open_bo < 0:
                open_bo = Decimal("0")
            db.session.delete(bo)
            db.session.flush()

        # Only put back qty that left physical layers (not still-open backorder debt)
        restore_qty = qty - open_bo
        if restore_qty > 0:
            cogs = Decimal(str(item.cost_of_goods or 0))
            unit_cost = (cogs / qty) if qty else Decimal("0")
            fifo_receive(
                product,
                restore_qty,
                unit_cost,
                "sale_return",
                sale.id,
                user_id,
                notes=f"Void sale {sale.invoice_no}",
                sale_price=item.unit_price,
                entry_at=sale.sale_date,
            )
        else:
            sync_product_stock(product)

    reverse_cash_by_reference(
        "sale",
        sale.id,
        notes=f"Void sale {sale.invoice_no}",
        created_by_id=user_id,
    )

    parties = delete_ledger_by_reference("sale", sale.id, rebuild=False)
    invoice = sale.invoice_no
    cid = sale.customer_id
    db.session.delete(sale)
    db.session.flush()

    if cid:
        rebuild_party_balances("customer", cid)
    for party_type, party_id in parties:
        if not (party_type == "customer" and party_id == cid):
            rebuild_party_balances(party_type, party_id)

    log_audit("delete", "sale", sale_id, invoice)
    enqueue_sync("sales", sale_id, "delete")
    return True


def replace_sale(sale_id, data, items, user_id):
    """Fully replace a sale (void + recreate) keeping the same invoice number."""
    sale = db.session.get(Sale, sale_id)
    if not sale:
        raise ValueError("Sale not found.")
    invoice_no = sale.invoice_no
    void_sale(sale_id, user_id)
    payload = dict(data or {})
    payload["invoice_no"] = invoice_no
    return create_sale(payload, items, user_id)

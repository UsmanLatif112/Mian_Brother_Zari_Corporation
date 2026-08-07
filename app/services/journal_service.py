from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models import (
    CashBookEntry,
    CustomerReceiving,
    Expense,
    ExpenseSettlement,
    Purchase,
    Sale,
    VendorPayment,
)
from app.services.dashboard_service import _range_for_filter


def _d(value):
    return Decimal(str(value or 0))


def _fmt_qty(value) -> str:
    text = f"{_d(value):.3f}".rstrip("0").rstrip(".")
    return text or "0"


def _fmt_particular(*parts) -> str:
    """Join particulars as: name / text / text."""
    cleaned = [str(p).strip() for p in parts if p is not None and str(p).strip()]
    return " / ".join(cleaned)


def _sale_line_particular(it) -> str:
    """Sale: name / units / sold weight unit — e.g. fhgj / 0.75 / 300 g."""
    product = getattr(it, "product", None)
    name = (getattr(product, "name", None) or "Item").strip() or "Item"
    sale_weight = getattr(it, "sale_weight", None)
    weight_unit = (
        getattr(it, "weight_unit", None)
        or getattr(product, "weight_unit", None)
        or ""
    ).strip()
    qty = getattr(it, "quantity", None)
    unit_weight = getattr(product, "unit_weight", None)

    if sale_weight is not None and _d(sale_weight) > 0:
        unit = weight_unit or "kg"
        # Open sale: show sold weight only (avoid confusing 0.333 bag fraction)
        return _fmt_particular(name, f"{_fmt_qty(sale_weight)} {unit}")

    if unit_weight is not None and _d(unit_weight) > 0 and qty is not None and _d(qty) > 0:
        total_w = _d(unit_weight) * _d(qty)
        unit = weight_unit or "kg"
        return _fmt_particular(name, _fmt_qty(qty), f"{_fmt_qty(total_w)} {unit}")

    if qty is not None and _d(qty) > 0:
        return _fmt_particular(name, f"{_fmt_qty(qty)} units")
    return name


def _purchase_line_particular(it) -> str:
    """Purchase: name / qty × unit wt @ rate — e.g. sona / 60 × 50 kg @ 400.00."""
    from app.services.purchase_service import purchase_line_particular

    return purchase_line_particular(it)


def items_particulars(items, *, limit: int = 4, fallback: str = "", kind: str = "sale") -> str:
    """Particulars from line items only (no amounts — those are in money columns)."""
    formatter = _purchase_line_particular if kind == "purchase" else _sale_line_particular
    labels: list[str] = []
    for it in items or []:
        label = formatter(it)
        if label:
            labels.append(label)
    if not labels:
        return fallback
    if len(labels) <= limit:
        return " | ".join(labels)
    shown = " | ".join(labels[:limit])
    return f"{shown} | +{len(labels) - limit} more"


# Back-compat alias used inside this module
_items_particulars = items_particulars


def _row(
    entry_date,
    entry_type,
    reference,
    particulars,
    party,
    amount_in,
    amount_out,
    status="",
    link=None,
    sort_id=0,
    total_paid=None,
    invoice_total=None,
):
    paid = _d(total_paid) if total_paid is not None else (_d(amount_in) if _d(amount_in) > 0 else Decimal("0"))
    return {
        "date": entry_date,
        "type": entry_type,
        "reference": reference,
        "particulars": particulars,
        "party": party or "—",
        "amount_in": _d(amount_in),
        "amount_out": _d(amount_out),
        "total_paid": paid,
        "invoice_total": _d(invoice_total) if invoice_total is not None else None,
        "status": status,
        "link": link,
        "sort_id": sort_id,
    }


def get_general_journal(period="all", start_date=None, end_date=None):
    """
    Cash in/out for the period (no opening balances).
    Expenses: Out when spent; In when you replenish the till on settle.
    Sales: In = cash received; Out = unpaid credit.
    Purchases: Out = cash paid to vendor; unpaid not in In/Out (payable until paid).
    Customer advance/settle = In, loan = Out; vendor loan = In, vendor pay = Out.
    """
    range_start, range_end = _range_for_filter(period, start_date, end_date)
    rows = []

    # —— Sales (paid / partial / unpaid) ——
    sq = Sale.query
    if range_start:
        sq = sq.filter(Sale.sale_date >= range_start)
    if range_end:
        sq = sq.filter(Sale.sale_date <= range_end)
    for s in sq.order_by(Sale.sale_date.desc(), Sale.id.desc()).all():
        total = _d(s.grand_total)
        total_paid = _d(s.amount_paid)  # full cash received (may exceed invoice)
        applied = min(total_paid, total)
        due = total - applied if total > applied else Decimal("0")
        status = s.payment_status.value if s.payment_status else ""
        party = s.customer.name if s.customer else "Walk-in"
        note = _items_particulars(s.items, fallback="Sale")
        rows.append(
            _row(
                s.sale_date,
                "Sale",
                s.invoice_no,
                note,
                party,
                total_paid if total_paid > 0 else Decimal("0"),  # In = cash received
                due,  # Out = unpaid credit
                status.title(),
                f"/sales/{s.id}/invoice",
                s.id,
                total_paid=total_paid,
                invoice_total=total,
            )
        )

    # —— Purchases ——
    pq = Purchase.query
    if range_start:
        pq = pq.filter(Purchase.purchase_date >= range_start)
    if range_end:
        pq = pq.filter(Purchase.purchase_date <= range_end)
    for p in pq.order_by(Purchase.purchase_date.desc(), Purchase.id.desc()).all():
        total = _d(p.grand_total)
        paid = _d(p.amount_paid)
        status = p.payment_status.value if p.payment_status else ""
        party = p.vendor.name if p.vendor else "—"
        note = _items_particulars(p.items, fallback="Purchase", kind="purchase")
        rows.append(
            _row(
                p.purchase_date,
                "Purchase",
                p.invoice_no,
                note,
                party,
                Decimal("0"),  # unpaid purchase is payable, not cash In
                paid if paid > 0 else Decimal("0"),  # Out = cash paid to vendor
                status.title(),
                "/purchases/",
                p.id,
                total_paid=paid,
                invoice_total=total,
            )
        )

    # —— Expenses (cash out from till when spent) ——
    eq = Expense.query.filter(Expense.is_deleted.is_(False))
    if range_start:
        eq = eq.filter(Expense.expense_date >= range_start)
    if range_end:
        eq = eq.filter(Expense.expense_date <= range_end)
    for e in eq.order_by(Expense.expense_date.desc(), Expense.id.desc()).all():
        amt = _d(e.amount)
        cat = e.category.name if e.category else "Expense"
        status = "Settled" if e.is_settled else "Pending replenish"
        rows.append(
            _row(
                e.expense_date,
                "Expense",
                f"EXP-{e.id}",
                _fmt_particular(e.name or cat, cat if e.name and e.name != cat else None),
                cat,
                Decimal("0"),
                amt,
                status,
                "/expenses/",
                e.id,
            )
        )

    # —— Expense settle (cash in — money from pocket to counter) ——
    settlements = (
        ExpenseSettlement.query.join(Expense)
        .filter(Expense.is_deleted.is_(False))
        .order_by(ExpenseSettlement.settled_at.desc(), ExpenseSettlement.id.desc())
        .all()
    )
    for s in settlements:
        e = s.expense
        if not e:
            continue
        pay_date = s.settled_at.date() if s.settled_at else e.expense_date
        if range_start and pay_date < range_start:
            continue
        if range_end and pay_date > range_end:
            continue
        cat = e.category.name if e.category else "Expense"
        rows.append(
            _row(
                pay_date,
                "Expense Settle",
                f"EXP-{e.id}",
                _fmt_particular(
                    "Replenish till",
                    e.name or cat,
                    s.notes if s.notes else None,
                ),
                cat,
                _d(s.amount),
                Decimal("0"),
                "Cash In",
                "/expenses/",
                e.id * 10000 + s.id,
            )
        )

    # —— Customer receivings (cash in against account) ——
    rq = CustomerReceiving.query
    if range_start:
        rq = rq.filter(CustomerReceiving.receiving_date >= range_start)
    if range_end:
        rq = rq.filter(CustomerReceiving.receiving_date <= range_end)
    for r in rq.order_by(CustomerReceiving.receiving_date.desc(), CustomerReceiving.id.desc()).all():
        ptype = getattr(r, "payment_type", None) or "account_settle"
        label = {
            "advance": "Customer Advance",
            "loan": "Customer Loan",
            "account_settle": "Account Settle",
        }.get(ptype, "Customer Payment")
        is_loan = ptype == "loan"
        rows.append(
            _row(
                r.receiving_date,
                label,
                f"CR-{r.id}",
                _fmt_particular(label, r.notes if r.notes else None),
                r.customer.name if r.customer else "—",
                Decimal("0") if is_loan else _d(r.amount),
                _d(r.amount) if is_loan else Decimal("0"),
                label,
                f"/customers/{r.customer_id}" if r.customer_id else None,
                r.id,
            )
        )

    # —— Vendor payments ——
    vq = VendorPayment.query
    if range_start:
        vq = vq.filter(VendorPayment.payment_date >= range_start)
    if range_end:
        vq = vq.filter(VendorPayment.payment_date <= range_end)
    for v in vq.order_by(VendorPayment.payment_date.desc(), VendorPayment.id.desc()).all():
        ptype = getattr(v, "payment_type", None) or "account_settle"
        label = {
            "advance": "Vendor Advance",
            "loan": "Vendor Loan",
            "account_settle": "Vendor Settle",
        }.get(ptype, "Vendor Payment")
        is_loan = ptype == "loan"
        rows.append(
            _row(
                v.payment_date,
                label,
                f"VP-{v.id}",
                _fmt_particular(label, v.notes if v.notes else None),
                v.vendor.name if v.vendor else "—",
                _d(v.amount) if is_loan else Decimal("0"),
                Decimal("0") if is_loan else _d(v.amount),
                label,
                f"/vendors/{v.vendor_id}" if v.vendor_id else "/vendors/",
                v.id,
            )
        )

    # —— Other cash book movements (manual / misc) not already covered ——
    cq = CashBookEntry.query.filter(
        ~CashBookEntry.category.in_(
            [
                "sales_collection",
                "expense_spent",
                "expense_settlement",
                "customer_advance",
                "customer_settle",
                "customer_loan",
                "vendor_advance",
                "vendor_settle",
                "vendor_loan",
            ]
        ),
        ~CashBookEntry.category.like("void_%"),
    )
    if range_start:
        cq = cq.filter(CashBookEntry.entry_date >= range_start)
    if range_end:
        cq = cq.filter(CashBookEntry.entry_date <= range_end)
    for c in cq.order_by(CashBookEntry.entry_date.desc(), CashBookEntry.id.desc()).all():
        is_in = (c.entry_type or "").lower() == "in"
        cat_label = (c.category or "Cash movement").replace("_", " ").title()
        rows.append(
            _row(
                c.entry_date,
                "Cash In" if is_in else "Cash Out",
                f"CB-{c.id}",
                _fmt_particular(cat_label, c.notes if c.notes else None),
                "Cash Book",
                _d(c.amount) if is_in else Decimal("0"),
                Decimal("0") if is_in else _d(c.amount),
                cat_label,
                None,
                c.id,
            )
        )

    # Sort: newest first, each entry separate (no opening reset / carry)
    rows.sort(key=lambda r: (r["date"] or date.min, r["sort_id"]), reverse=True)

    total_in = sum((r["amount_in"] for r in rows), Decimal("0"))
    total_out = sum((r["amount_out"] for r in rows), Decimal("0"))

    return {
        "rows": rows,
        "total_in": total_in,
        "total_out": total_out,
        "net": total_in - total_out,
        "count": len(rows),
        "period_start": range_start,
        "period_end": range_end,
    }

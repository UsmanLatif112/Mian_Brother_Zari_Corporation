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
    Credit sales = Out; purchases = In; customer advance/settle = In, loan = Out;
    vendor loan = In, vendor pay = Out.
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
        advance = total_paid - applied if total_paid > applied else Decimal("0")
        status = s.payment_status.value if s.payment_status else ""
        party = s.customer.name if s.customer else "Walk-in"
        note = f"Sale total {total:.2f}"
        if _d(s.discount) > 0:
            note += f" · Discount {_d(s.discount):.2f}"
        if due > 0:
            note += f" · Credit {due:.2f}"
        if advance > 0:
            note += f" · Advance {advance:.2f}"
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
        due = total - paid if total > paid else Decimal("0")
        status = p.payment_status.value if p.payment_status else ""
        party = p.vendor.name if p.vendor else "—"
        note = f"Purchase total {total:.2f}"
        if due > 0:
            note += f" · Credit {due:.2f}"
        if paid > 0:
            note += f" · Paid at purchase {paid:.2f}"
        rows.append(
            _row(
                p.purchase_date,
                "Purchase",
                p.invoice_no,
                note,
                party,
                total,
                Decimal("0"),
                status.title(),
                "/purchases/",
                p.id,
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
                e.name or cat,
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
                s.notes or f"Replenish till: {e.name or cat}",
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
                r.notes or label,
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
                v.notes or label,
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
        rows.append(
            _row(
                c.entry_date,
                "Cash In" if is_in else "Cash Out",
                f"CB-{c.id}",
                c.notes or (c.category or "Cash movement").replace("_", " ").title(),
                "Cash Book",
                _d(c.amount) if is_in else Decimal("0"),
                Decimal("0") if is_in else _d(c.amount),
                (c.category or "").replace("_", " ").title(),
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

"""Account summary pie data for customer and vendor detail pages."""

from __future__ import annotations

from decimal import Decimal

from app.extensions import db
from app.models import LedgerEntry


def _pie_payload(items):
    labels = [i["name"] for i in items]
    values = [float(i.get("value") or 0) for i in items]
    return {"labels": labels, "values": values, "rows": items}


def _ledger_entries(party_type: str, party_id: int, range_start=None, range_end=None):
    q = LedgerEntry.query.filter_by(party_type=party_type, party_id=party_id)
    if range_start:
        q = q.filter(LedgerEntry.entry_date >= range_start)
    if range_end:
        q = q.filter(LedgerEntry.entry_date <= range_end)
    return q.all()


def _sum_debit(entries, *entry_types: str) -> Decimal:
    types = {t.lower() for t in entry_types}
    return sum(
        (Decimal(str(e.debit or 0)) for e in entries if (e.entry_type or "").lower() in types),
        Decimal("0"),
    )


def _sum_credit(entries, *entry_types: str) -> Decimal:
    types = {t.lower() for t in entry_types}
    return sum(
        (Decimal(str(e.credit or 0)) for e in entries if (e.entry_type or "").lower() in types),
        Decimal("0"),
    )


def customer_account_pie(customer_id: int, *, range_start=None, range_end=None, display_balance=None):
    """Pie slices: total sale, total paid, credit owing, advance (if any)."""
    entries = _ledger_entries("customer", customer_id, range_start, range_end)
    period_hint = "In period" if range_start or range_end else "All time"

    total_sale = _sum_debit(entries, "sale") - _sum_credit(entries, "sale_return")
    if total_sale < 0:
        total_sale = Decimal("0")

    paid_at_sale = _sum_credit(entries, "sale")
    payments = _sum_credit(entries, "account_settle", "advance", "sale_advance")
    refunds = _sum_debit(entries, "sale_return")
    total_paid = paid_at_sale + payments - refunds
    if total_paid < 0:
        total_paid = Decimal("0")

    bal = Decimal(str(display_balance if display_balance is not None else 0))
    total_credit = max(bal, Decimal("0"))
    total_advance = max(-bal, Decimal("0"))

    rows = []
    if total_sale > 0:
        rows.append({"name": "Total Sale", "value": float(total_sale), "detail": period_hint})
    if total_paid > 0:
        rows.append({"name": "Total Paid", "value": float(total_paid), "detail": period_hint})
    if total_credit > 0:
        rows.append({"name": "Total Credit", "value": float(total_credit), "detail": "Outstanding"})
    if total_advance > 0:
        rows.append({"name": "Advance", "value": float(total_advance), "detail": "With us"})

    return {
        "pie": _pie_payload(rows),
        "total_sale": total_sale,
        "total_paid": total_paid,
        "total_credit": total_credit,
        "total_advance": total_advance,
    }


def vendor_account_pie(vendor_id: int, *, range_start=None, range_end=None, display_balance=None):
    """Pie slices: total purchase, total paid, prepaid credit, payable (if any)."""
    entries = _ledger_entries("vendor", vendor_id, range_start, range_end)
    period_hint = "In period" if range_start or range_end else "All time"

    total_purchase = _sum_debit(entries, "purchase")
    total_paid = _sum_credit(entries, "account_settle", "advance")
    for entry in entries:
        if (entry.entry_type or "").lower() == "purchase":
            total_paid += Decimal(str(entry.credit or 0))

    bal = Decimal(str(display_balance if display_balance is not None else 0))
    total_payable = max(bal, Decimal("0"))
    total_credit = max(-bal, Decimal("0"))

    rows = []
    if total_purchase > 0:
        rows.append({"name": "Total Purchase", "value": float(total_purchase), "detail": period_hint})
    if total_paid > 0:
        rows.append({"name": "Total Paid", "value": float(total_paid), "detail": period_hint})
    if total_credit > 0:
        rows.append({"name": "Prepaid Credit", "value": float(total_credit), "detail": "Advance with vendor"})
    if total_payable > 0:
        rows.append({"name": "Total Payable", "value": float(total_payable), "detail": "Outstanding"})

    return {
        "pie": _pie_payload(rows),
        "total_purchase": total_purchase,
        "total_paid": total_paid,
        "total_credit": total_credit,
        "total_payable": total_payable,
    }

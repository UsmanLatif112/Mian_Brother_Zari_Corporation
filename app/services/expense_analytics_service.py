"""Expense summary and KPI pie for the expenses page."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func

from app.extensions import db
from app.models import Expense


def _pie_payload(items):
    labels = [i["name"] for i in items]
    values = [float(i.get("value") or 0) for i in items]
    return {"labels": labels, "values": values, "rows": items}


def expense_page_analytics(*, range_start=None, range_end=None, category_id=None):
    """KPI totals plus pie slices matching the four summary lines."""
    base_filters = [Expense.is_deleted.is_(False)]
    if range_start:
        base_filters.append(Expense.expense_date >= range_start)
    if range_end:
        base_filters.append(Expense.expense_date <= range_end)
    if category_id:
        base_filters.append(Expense.category_id == category_id)

    period_hint = "In period" if range_start or range_end else "All time"

    total_amount = (
        db.session.query(func.coalesce(func.sum(Expense.amount), 0))
        .filter(*base_filters)
        .scalar()
    ) or Decimal("0")
    settled_amount = (
        db.session.query(func.coalesce(func.sum(Expense.amount), 0))
        .filter(*base_filters, Expense.is_settled.is_(True))
        .scalar()
    ) or Decimal("0")
    pending_amount = (
        db.session.query(func.coalesce(func.sum(Expense.amount), 0))
        .filter(*base_filters, Expense.is_settled.is_(False))
        .scalar()
    ) or Decimal("0")
    pending_count = Expense.query.filter(*base_filters, Expense.is_settled.is_(False)).count()
    total_count = Expense.query.filter(*base_filters).count()

    kpis = [
        {"name": "Total Expenses", "value": float(total_amount), "detail": period_hint},
        {"name": "Pending Amount", "value": float(pending_amount), "detail": period_hint},
        {"name": "Settled Amount", "value": float(settled_amount), "detail": period_hint},
        {
            "name": "Pending Items",
            "value": float(pending_count),
            "detail": f"{total_count} record{'s' if total_count != 1 else ''} total",
            "is_count": True,
        },
    ]

    pie_rows = [dict(k) for k in kpis if float(k.get("value") or 0) > 0]

    return {
        "total_amount": total_amount,
        "settled_amount": settled_amount,
        "pending_amount": pending_amount,
        "pending_count": pending_count,
        "total_count": total_count,
        "kpis": kpis,
        "pie": _pie_payload(pie_rows),
    }

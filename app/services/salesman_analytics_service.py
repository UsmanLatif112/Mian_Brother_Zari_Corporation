"""Per-salesman sales, paid, and credit chart for the salesman detail page."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func

from app.extensions import db
from app.models import LedgerEntry, Sale
from app.services.dashboard_service import (
    _format_chart_axis_label,
    _iter_chart_bucket_keys,
)
from app.utils.working_date import get_working_date


def _earliest_salesman_activity_date(salesman_id: int) -> date:
    today = get_working_date()
    candidates: list[date] = []

    sale_min = (
        db.session.query(func.min(Sale.sale_date))
        .filter(Sale.salesman_id == salesman_id)
        .scalar()
    )
    if sale_min:
        candidates.append(sale_min)

    ledger_min = (
        db.session.query(func.min(LedgerEntry.entry_date))
        .filter(LedgerEntry.party_type == "salesman", LedgerEntry.party_id == salesman_id)
        .scalar()
    )
    if ledger_min:
        candidates.append(ledger_min)

    if not candidates:
        return today - timedelta(days=29)
    return min(candidates)


def _salesman_chart_range(start, end, *, period: str, salesman_id: int):
    end = end or get_working_date()
    if not start:
        if period == "all":
            start = _earliest_salesman_activity_date(salesman_id)
        else:
            start = end - timedelta(days=364)
    span = (end - start).days
    daily = span <= 31
    group_fmt = "%Y-%m-%d" if daily else "%Y-%m"
    return start, end, group_fmt, daily


def _ledger_bucket_map(salesman_id, group_fmt, chart_start, chart_end, field, *entry_types):
    types = list(entry_types)
    q = db.session.query(
        func.strftime(group_fmt, LedgerEntry.entry_date),
        func.coalesce(func.sum(getattr(LedgerEntry, field)), 0),
    ).filter(
        LedgerEntry.party_type == "salesman",
        LedgerEntry.party_id == salesman_id,
        LedgerEntry.entry_date >= chart_start,
        LedgerEntry.entry_date <= chart_end,
        LedgerEntry.entry_type.in_(types),
    )
    return {r[0]: float(r[1] or 0) for r in q.group_by(func.strftime(group_fmt, LedgerEntry.entry_date)).all()}


def _sum_ledger(salesman_id, range_start, range_end, field, *entry_types):
    q = db.session.query(func.coalesce(func.sum(getattr(LedgerEntry, field)), 0)).filter(
        LedgerEntry.party_type == "salesman",
        LedgerEntry.party_id == salesman_id,
        LedgerEntry.entry_type.in_(list(entry_types)),
    )
    if range_start:
        q = q.filter(LedgerEntry.entry_date >= range_start)
    if range_end:
        q = q.filter(LedgerEntry.entry_date <= range_end)
    return Decimal(str(q.scalar() or 0))


def salesman_performance_metrics(salesman_id: int, *, period: str = "all", start=None, end=None):
    """Chart series: total sales, paid, and credit (unpaid) over time."""
    chart_start, chart_end, group_fmt, chart_daily = _salesman_chart_range(
        start, end, period=period, salesman_id=salesman_id
    )

    sale_debits = _ledger_bucket_map(salesman_id, group_fmt, chart_start, chart_end, "debit", "sale")
    sale_credits = _ledger_bucket_map(salesman_id, group_fmt, chart_start, chart_end, "credit", "sale")
    return_debits = _ledger_bucket_map(
        salesman_id, group_fmt, chart_start, chart_end, "debit", "sale_return"
    )
    return_credits = _ledger_bucket_map(
        salesman_id, group_fmt, chart_start, chart_end, "credit", "sale_return"
    )

    bucket_keys = _iter_chart_bucket_keys(chart_start, chart_end, daily=chart_daily)
    if chart_daily and len(bucket_keys) > 62:
        bucket_keys = bucket_keys[-62:]
    elif period != "all" and not chart_daily and len(bucket_keys) > 24:
        bucket_keys = bucket_keys[-24:]

    chart_labels = []
    chart_sales = []
    chart_paid = []
    chart_credit = []
    for bucket in bucket_keys:
        sales_f = sale_debits.get(bucket, 0.0) - return_credits.get(bucket, 0.0)
        paid_f = sale_credits.get(bucket, 0.0) - return_debits.get(bucket, 0.0)
        credit_f = sales_f - paid_f
        chart_labels.append(_format_chart_axis_label(bucket, daily=chart_daily))
        chart_sales.append(sales_f)
        chart_paid.append(paid_f)
        chart_credit.append(credit_f)

    first_active = 0
    for i in range(len(bucket_keys)):
        if chart_sales[i] or chart_paid[i] or chart_credit[i]:
            first_active = i
            break
    if first_active > 0:
        chart_labels = chart_labels[first_active:]
        chart_sales = chart_sales[first_active:]
        chart_paid = chart_paid[first_active:]
        chart_credit = chart_credit[first_active:]

    period_sale_debit = _sum_ledger(salesman_id, start, end, "debit", "sale")
    period_return_credit = _sum_ledger(salesman_id, start, end, "credit", "sale_return")
    period_sale_credit = _sum_ledger(salesman_id, start, end, "credit", "sale")
    period_return_debit = _sum_ledger(salesman_id, start, end, "debit", "sale_return")

    total_sales = period_sale_debit - period_return_credit
    total_paid = period_sale_credit - period_return_debit
    total_credit = total_sales - total_paid

    return {
        "total_sales": total_sales,
        "total_paid": total_paid,
        "total_credit": total_credit,
        "chart": {
            "labels": chart_labels,
            "sales": chart_sales,
            "paid": chart_paid,
            "credit": chart_credit,
            "granularity": "day" if chart_daily else "month",
        },
    }

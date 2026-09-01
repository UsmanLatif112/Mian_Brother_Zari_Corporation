"""Customer list page chart metrics (sale, paid, credit over time)."""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func

from app.extensions import db
from app.models import LedgerEntry, Sale
from app.services.dashboard_service import (
    _format_chart_axis_label,
    _iter_chart_bucket_keys,
    _range_for_filter,
)
from app.services.ledger_service import sum_party_balances_as_of
from app.utils.working_date import get_working_date


def _earliest_customer_activity_date() -> date:
    today = get_working_date()
    candidates: list[date] = []
    for raw in (
        db.session.query(func.min(Sale.sale_date))
        .filter(Sale.customer_id.isnot(None))
        .scalar(),
        db.session.query(func.min(LedgerEntry.entry_date))
        .filter(LedgerEntry.party_type == "customer")
        .scalar(),
    ):
        if raw:
            candidates.append(raw)
    if not candidates:
        return today - timedelta(days=29)
    return min(candidates)


def _customer_chart_range(start, end, *, period: str):
    end = end or get_working_date()
    if not start:
        if period == "all":
            start = _earliest_customer_activity_date()
        else:
            start = end - timedelta(days=364)
    span = (end - start).days
    daily = span <= 31
    group_fmt = "%Y-%m-%d" if daily else "%Y-%m"
    return start, end, group_fmt, daily


def _bucket_end(bucket: str, *, daily: bool, chart_end: date) -> date:
    if daily:
        d = date.fromisoformat(bucket)
    else:
        y, m = map(int, bucket.split("-"))
        d = date(y, m, calendar.monthrange(y, m)[1])
    return min(d, chart_end)


def _ledger_bucket_map(
    group_fmt,
    chart_start,
    chart_end,
    field,
    *entry_types,
    party_ids=None,
):
    q = db.session.query(
        func.strftime(group_fmt, LedgerEntry.entry_date),
        func.coalesce(func.sum(getattr(LedgerEntry, field)), 0),
    ).filter(
        LedgerEntry.party_type == "customer",
        LedgerEntry.entry_date >= chart_start,
        LedgerEntry.entry_date <= chart_end,
        LedgerEntry.entry_type.in_(list(entry_types)),
    )
    if party_ids is not None:
        if not party_ids:
            return {}
        q = q.filter(LedgerEntry.party_id.in_(party_ids))
    return {r[0]: float(r[1] or 0) for r in q.group_by(func.strftime(group_fmt, LedgerEntry.entry_date)).all()}


def _sum_ledger(range_start, range_end, field, *entry_types, party_ids=None):
    q = db.session.query(func.coalesce(func.sum(getattr(LedgerEntry, field)), 0)).filter(
        LedgerEntry.party_type == "customer",
        LedgerEntry.entry_type.in_(list(entry_types)),
    )
    if party_ids is not None:
        if not party_ids:
            return Decimal("0")
        q = q.filter(LedgerEntry.party_id.in_(party_ids))
    if range_start:
        q = q.filter(LedgerEntry.entry_date >= range_start)
    if range_end:
        q = q.filter(LedgerEntry.entry_date <= range_end)
    return Decimal(str(q.scalar() or 0))


def customer_list_chart_metrics(
    *, period: str = "all", start_date=None, end_date=None, party_ids=None
):
    """Line chart aligned with customer list KPIs."""
    range_start, range_end = _range_for_filter(period, start_date, end_date)
    chart_start, chart_end, group_fmt, chart_daily = _customer_chart_range(
        range_start, range_end, period=period
    )

    sale_debits = _ledger_bucket_map(
        group_fmt, chart_start, chart_end, "debit", "sale", party_ids=party_ids
    )
    sale_credits = _ledger_bucket_map(
        group_fmt, chart_start, chart_end, "credit", "sale", party_ids=party_ids
    )
    return_debits = _ledger_bucket_map(
        group_fmt, chart_start, chart_end, "debit", "sale_return", party_ids=party_ids
    )
    return_credits = _ledger_bucket_map(
        group_fmt, chart_start, chart_end, "credit", "sale_return", party_ids=party_ids
    )
    settle_credits = _ledger_bucket_map(
        group_fmt,
        chart_start,
        chart_end,
        "credit",
        "account_settle",
        "advance",
        "sale_advance",
        party_ids=party_ids,
    )

    sale_count_q = db.session.query(
        func.strftime(group_fmt, Sale.sale_date),
        func.count(Sale.id),
    ).filter(
        Sale.customer_id.isnot(None),
        Sale.sale_date >= chart_start,
        Sale.sale_date <= chart_end,
    )
    if party_ids is not None:
        if not party_ids:
            sale_count_map = {}
        else:
            sale_count_q = sale_count_q.filter(Sale.customer_id.in_(party_ids))
            sale_count_map = {
                r[0]: int(r[1] or 0)
                for r in sale_count_q.group_by(func.strftime(group_fmt, Sale.sale_date)).all()
            }
    else:
        sale_count_map = {
            r[0]: int(r[1] or 0)
            for r in sale_count_q.group_by(func.strftime(group_fmt, Sale.sale_date)).all()
        }

    bucket_keys = _iter_chart_bucket_keys(chart_start, chart_end, daily=chart_daily)
    if chart_daily and len(bucket_keys) > 62:
        bucket_keys = bucket_keys[-62:]
    elif period != "all" and not chart_daily and len(bucket_keys) > 24:
        bucket_keys = bucket_keys[-24:]

    chart_labels = []
    chart_sale = []
    chart_paid = []
    chart_credit = []
    for bucket in bucket_keys:
        sale_f = sale_debits.get(bucket, 0.0) - return_credits.get(bucket, 0.0)
        paid_f = (
            sale_credits.get(bucket, 0.0)
            - return_debits.get(bucket, 0.0)
            + settle_credits.get(bucket, 0.0)
        )
        as_of = _bucket_end(bucket, daily=chart_daily, chart_end=chart_end)
        credit_f, _ = sum_party_balances_as_of("customer", as_of, party_ids=party_ids)
        chart_labels.append(_format_chart_axis_label(bucket, daily=chart_daily))
        chart_sale.append(sale_f)
        chart_paid.append(paid_f)
        chart_credit.append(float(credit_f))

    first_active = 0
    for i in range(len(bucket_keys)):
        if chart_sale[i] or chart_paid[i] or chart_credit[i]:
            first_active = i
            break
    if first_active > 0:
        chart_labels = chart_labels[first_active:]
        chart_sale = chart_sale[first_active:]
        chart_paid = chart_paid[first_active:]
        chart_credit = chart_credit[first_active:]

    period_sale_debit = _sum_ledger(
        range_start, range_end, "debit", "sale", party_ids=party_ids
    )
    period_return_credit = _sum_ledger(
        range_start, range_end, "credit", "sale_return", party_ids=party_ids
    )
    period_sale_credit = _sum_ledger(
        range_start, range_end, "credit", "sale", party_ids=party_ids
    )
    period_return_debit = _sum_ledger(
        range_start, range_end, "debit", "sale_return", party_ids=party_ids
    )
    period_settle = _sum_ledger(
        range_start,
        range_end,
        "credit",
        "account_settle",
        "advance",
        "sale_advance",
        party_ids=party_ids,
    )
    total_sale = period_sale_debit - period_return_credit
    total_paid = period_sale_credit - period_return_debit + period_settle

    return {
        "total_sale": total_sale,
        "total_paid": total_paid,
        "chart": {
            "labels": chart_labels,
            "sale": chart_sale,
            "paid": chart_paid,
            "credit": chart_credit,
            "granularity": "day" if chart_daily else "month",
        },
    }

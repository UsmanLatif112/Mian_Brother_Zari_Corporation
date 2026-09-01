"""Account page chart metrics (amount taken & balance chain over time)."""

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func

from app.extensions import db
from app.models import AccountAmountTaken, AccountCashSetup, Expense
from app.services.cashbook_service import get_cash_dashboard_metrics, period_cash_collections
from app.services.dashboard_service import (
    _earliest_dashboard_activity_date,
    _format_chart_axis_label,
    _iter_chart_bucket_keys,
    _range_for_filter,
)
from app.utils.working_date import get_working_date


def _earliest_taken_date() -> date:
    today = get_working_date()
    raw = (
        db.session.query(func.min(AccountAmountTaken.taken_date))
        .filter(AccountAmountTaken.is_deleted.is_(False))
        .scalar()
    )
    if raw:
        return raw
    setup = (
        db.session.query(func.min(AccountCashSetup.balance_date)).scalar()
    )
    if setup:
        return setup
    return today - timedelta(days=29)


def _account_chart_range(start, end, *, period: str):
    end = end or get_working_date()
    if not start:
        if period == "all":
            start = _earliest_taken_date()
        else:
            start = end - timedelta(days=364)
    span = (end - start).days
    daily = span <= 31
    group_fmt = "%Y-%m-%d" if daily else "%Y-%m"
    return start, end, group_fmt, daily


def _balance_at_start(as_of: date) -> float:
    last = (
        AccountAmountTaken.query.filter_by(is_deleted=False)
        .filter(AccountAmountTaken.taken_date < as_of)
        .order_by(AccountAmountTaken.taken_date.desc(), AccountAmountTaken.id.desc())
        .first()
    )
    if last:
        return float(last.balance_after or 0)
    setup = (
        AccountCashSetup.query.filter(AccountCashSetup.balance_date <= as_of)
        .order_by(AccountCashSetup.balance_date.desc(), AccountCashSetup.id.desc())
        .first()
    )
    if setup:
        return float(setup.previous_balance or 0)
    return 0.0


def _bucket_end(bucket: str, *, daily: bool, chart_end: date) -> date:
    if daily:
        d = date.fromisoformat(bucket)
    else:
        y, m = map(int, bucket.split("-"))
        d = date(y, m, calendar.monthrange(y, m)[1])
    return min(d, chart_end)


def _cash_in_hand_as_of(as_of: date, previous: float) -> float:
    """Same formula as account KPI: journal In + previous balance − expense (cumulative)."""
    cash_start = _earliest_dashboard_activity_date()
    collections = period_cash_collections(cash_start, as_of)
    total_expense = (
        db.session.query(func.coalesce(func.sum(Expense.amount), 0))
        .filter(
            Expense.is_deleted.is_(False),
            Expense.expense_date >= cash_start,
            Expense.expense_date <= as_of,
        )
        .scalar()
        or 0
    )
    cash = get_cash_dashboard_metrics(
        total_sale=collections,
        previous_amount=Decimal(str(previous)),
        total_expense=total_expense,
    )["cash_in_hand"]
    return float(cash)


def _period_taken_total(range_start, range_end) -> Decimal:
    q = db.session.query(func.coalesce(func.sum(AccountAmountTaken.amount), 0)).filter(
        AccountAmountTaken.is_deleted.is_(False)
    )
    if range_start:
        q = q.filter(AccountAmountTaken.taken_date >= range_start)
    if range_end:
        q = q.filter(AccountAmountTaken.taken_date <= range_end)
    return Decimal(str(q.scalar() or 0))


def account_page_chart_metrics(*, period: str = "all", start_date=None, end_date=None):
    """Line chart aligned with account page: taken, previous balance, balance after."""
    range_start, range_end = _range_for_filter(period, start_date, end_date)
    chart_start, chart_end, group_fmt, chart_daily = _account_chart_range(
        range_start, range_end, period=period
    )

    taken_map = {
        r[0]: float(r[1] or 0)
        for r in db.session.query(
            func.strftime(group_fmt, AccountAmountTaken.taken_date),
            func.coalesce(func.sum(AccountAmountTaken.amount), 0),
        )
        .filter(
            AccountAmountTaken.is_deleted.is_(False),
            AccountAmountTaken.taken_date >= chart_start,
            AccountAmountTaken.taken_date <= chart_end,
        )
        .group_by(func.strftime(group_fmt, AccountAmountTaken.taken_date))
        .all()
    }
    count_map = {
        r[0]: int(r[1] or 0)
        for r in db.session.query(
            func.strftime(group_fmt, AccountAmountTaken.taken_date),
            func.count(AccountAmountTaken.id),
        )
        .filter(
            AccountAmountTaken.is_deleted.is_(False),
            AccountAmountTaken.taken_date >= chart_start,
            AccountAmountTaken.taken_date <= chart_end,
        )
        .group_by(func.strftime(group_fmt, AccountAmountTaken.taken_date))
        .all()
    }

    takes_by_bucket: dict[str, list] = defaultdict(list)
    takes = (
        AccountAmountTaken.query.filter_by(is_deleted=False)
        .filter(
            AccountAmountTaken.taken_date >= chart_start,
            AccountAmountTaken.taken_date <= chart_end,
        )
        .order_by(AccountAmountTaken.taken_date.asc(), AccountAmountTaken.id.asc())
        .all()
    )
    for row in takes:
        if chart_daily:
            bucket = row.taken_date.strftime("%Y-%m-%d")
        else:
            bucket = f"{row.taken_date.year:04d}-{row.taken_date.month:02d}"
        takes_by_bucket[bucket].append(row)

    bucket_keys = _iter_chart_bucket_keys(chart_start, chart_end, daily=chart_daily)
    if chart_daily and len(bucket_keys) > 62:
        bucket_keys = bucket_keys[-62:]
    elif period != "all" and not chart_daily and len(bucket_keys) > 24:
        bucket_keys = bucket_keys[-24:]

    running = _balance_at_start(chart_start)
    chart_labels = []
    chart_taken = []
    chart_previous = []
    chart_after = []
    chart_cash = []
    chart_count = []
    for bucket in bucket_keys:
        bucket_takes = takes_by_bucket.get(bucket, [])
        taken_f = taken_map.get(bucket, 0.0)
        count_f = float(count_map.get(bucket, 0))
        if bucket_takes:
            prev_f = float(bucket_takes[0].previous_balance or 0)
            after_f = float(bucket_takes[-1].balance_after or 0)
            running = after_f
        else:
            prev_f = running
            after_f = running
        as_of = _bucket_end(bucket, daily=chart_daily, chart_end=chart_end)
        cash_f = _cash_in_hand_as_of(as_of, after_f)
        chart_labels.append(_format_chart_axis_label(bucket, daily=chart_daily))
        chart_taken.append(taken_f)
        chart_previous.append(prev_f)
        chart_after.append(after_f)
        chart_cash.append(cash_f)
        chart_count.append(count_f)

    first_active = 0
    for i in range(len(bucket_keys)):
        if chart_taken[i] or chart_count[i] or chart_cash[i]:
            first_active = i
            break
    if first_active > 0:
        chart_labels = chart_labels[first_active:]
        chart_taken = chart_taken[first_active:]
        chart_previous = chart_previous[first_active:]
        chart_after = chart_after[first_active:]
        chart_cash = chart_cash[first_active:]
        chart_count = chart_count[first_active:]

    filtered_total = _period_taken_total(range_start, range_end)

    return {
        "filtered_total": filtered_total,
        "chart": {
            "labels": chart_labels,
            "taken": chart_taken,
            "previous": chart_previous,
            "after": chart_after,
            "cash": chart_cash,
            "count": chart_count,
            "granularity": "day" if chart_daily else "month",
        },
    }

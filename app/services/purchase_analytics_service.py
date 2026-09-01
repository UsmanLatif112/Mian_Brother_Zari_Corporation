"""Purchase page chart metrics (purchasing, paid, payable over time)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func

from app.extensions import db
from app.models import Purchase
from app.services.dashboard_service import (
    _format_chart_axis_label,
    _iter_chart_bucket_keys,
    _range_for_filter,
)
from app.utils.working_date import get_working_date


def _earliest_purchase_date() -> date:
    today = get_working_date()
    raw = db.session.query(func.min(Purchase.purchase_date)).scalar()
    if raw:
        return raw
    return today - timedelta(days=29)


def _purchase_chart_range(start, end, *, period: str):
    end = end or get_working_date()
    if not start:
        if period == "all":
            start = _earliest_purchase_date()
        else:
            start = end - timedelta(days=364)
    span = (end - start).days
    daily = span <= 31
    group_fmt = "%Y-%m-%d" if daily else "%Y-%m"
    return start, end, group_fmt, daily


def _period_totals(range_start, range_end):
    q = db.session.query(
        func.coalesce(func.sum(Purchase.grand_total), 0),
        func.coalesce(func.sum(Purchase.amount_paid), 0),
        func.count(Purchase.id),
    )
    if range_start:
        q = q.filter(Purchase.purchase_date >= range_start)
    if range_end:
        q = q.filter(Purchase.purchase_date <= range_end)
    total_purchasing, total_paid, purchase_count = q.one()
    total_purchasing = Decimal(str(total_purchasing or 0))
    total_paid = Decimal(str(total_paid or 0))
    total_payable = max(total_purchasing - total_paid, Decimal("0"))
    return total_purchasing, total_paid, total_payable, int(purchase_count or 0)


def purchase_page_chart_metrics(*, period: str = "all", start_date=None, end_date=None):
    """Line chart aligned with purchases page KPIs."""
    range_start, range_end = _range_for_filter(period, start_date, end_date)
    chart_start, chart_end, group_fmt, chart_daily = _purchase_chart_range(
        range_start, range_end, period=period
    )

    amount_map = {
        r[0]: float(r[1] or 0)
        for r in db.session.query(
            func.strftime(group_fmt, Purchase.purchase_date),
            func.coalesce(func.sum(Purchase.grand_total), 0),
        )
        .filter(Purchase.purchase_date >= chart_start, Purchase.purchase_date <= chart_end)
        .group_by(func.strftime(group_fmt, Purchase.purchase_date))
        .all()
    }
    paid_map = {
        r[0]: float(r[1] or 0)
        for r in db.session.query(
            func.strftime(group_fmt, Purchase.purchase_date),
            func.coalesce(func.sum(Purchase.amount_paid), 0),
        )
        .filter(Purchase.purchase_date >= chart_start, Purchase.purchase_date <= chart_end)
        .group_by(func.strftime(group_fmt, Purchase.purchase_date))
        .all()
    }
    count_map = {
        r[0]: int(r[1] or 0)
        for r in db.session.query(
            func.strftime(group_fmt, Purchase.purchase_date),
            func.count(Purchase.id),
        )
        .filter(Purchase.purchase_date >= chart_start, Purchase.purchase_date <= chart_end)
        .group_by(func.strftime(group_fmt, Purchase.purchase_date))
        .all()
    }

    bucket_keys = _iter_chart_bucket_keys(chart_start, chart_end, daily=chart_daily)
    if chart_daily and len(bucket_keys) > 62:
        bucket_keys = bucket_keys[-62:]
    elif period != "all" and not chart_daily and len(bucket_keys) > 24:
        bucket_keys = bucket_keys[-24:]

    chart_labels = []
    chart_total = []
    chart_paid = []
    chart_payable = []
    chart_count = []
    for bucket in bucket_keys:
        total_f = amount_map.get(bucket, 0.0)
        paid_f = paid_map.get(bucket, 0.0)
        payable_f = max(total_f - paid_f, 0.0)
        count_f = float(count_map.get(bucket, 0))
        chart_labels.append(_format_chart_axis_label(bucket, daily=chart_daily))
        chart_total.append(total_f)
        chart_paid.append(paid_f)
        chart_payable.append(payable_f)
        chart_count.append(count_f)

    first_active = 0
    for i in range(len(bucket_keys)):
        if chart_total[i] or chart_paid[i] or chart_payable[i] or chart_count[i]:
            first_active = i
            break
    if first_active > 0:
        chart_labels = chart_labels[first_active:]
        chart_total = chart_total[first_active:]
        chart_paid = chart_paid[first_active:]
        chart_payable = chart_payable[first_active:]
        chart_count = chart_count[first_active:]

    total_purchasing, total_paid, total_payable, purchase_count = _period_totals(
        range_start, range_end
    )

    return {
        "total_purchasing": total_purchasing,
        "total_paid": total_paid,
        "total_payable": total_payable,
        "purchase_count": purchase_count,
        "chart": {
            "labels": chart_labels,
            "total": chart_total,
            "paid": chart_paid,
            "payable": chart_payable,
            "count": chart_count,
            "granularity": "day" if chart_daily else "month",
        },
    }

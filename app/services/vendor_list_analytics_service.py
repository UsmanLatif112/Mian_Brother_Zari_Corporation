"""Vendor list page chart metrics (purchase, paid, payable over time)."""

from __future__ import annotations

import calendar
from datetime import date

from sqlalchemy import func

from app.extensions import db
from app.models import Purchase
from app.services.dashboard_service import (
    _format_chart_axis_label,
    _iter_chart_bucket_keys,
    _range_for_filter,
)
from app.services.ledger_service import sum_party_balances_as_of
from app.services.purchase_analytics_service import (
    _period_totals,
    _purchase_chart_range,
)


def _bucket_end(bucket: str, *, daily: bool, chart_end: date) -> date:
    if daily:
        d = date.fromisoformat(bucket)
    else:
        y, m = map(int, bucket.split("-"))
        d = date(y, m, calendar.monthrange(y, m)[1])
    return min(d, chart_end)


def vendor_list_chart_metrics(*, period: str = "all", start_date=None, end_date=None):
    """Line chart aligned with vendor list KPIs."""
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
        as_of = _bucket_end(bucket, daily=chart_daily, chart_end=chart_end)
        payable_f, _prepaid = sum_party_balances_as_of("vendor", as_of)
        count_f = float(count_map.get(bucket, 0))
        chart_labels.append(_format_chart_axis_label(bucket, daily=chart_daily))
        chart_total.append(total_f)
        chart_paid.append(paid_f)
        chart_payable.append(float(payable_f))
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

    total_purchasing, total_paid, total_payable, _purchase_count = _period_totals(
        range_start, range_end
    )

    return {
        "total_purchasing": total_purchasing,
        "total_paid": total_paid,
        "total_payable": total_payable,
        "chart": {
            "labels": chart_labels,
            "total": chart_total,
            "paid": chart_paid,
            "payable": chart_payable,
            "count": chart_count,
            "granularity": "day" if chart_daily else "month",
        },
    }

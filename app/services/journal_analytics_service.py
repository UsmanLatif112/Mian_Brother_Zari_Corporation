"""General journal chart metrics (cash in / out / net over time)."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from app.services.dashboard_service import (
    _earliest_dashboard_activity_date,
    _format_chart_axis_label,
    _iter_chart_bucket_keys,
)
from app.services.journal_service import get_general_journal
from app.utils.working_date import get_working_date


def _journal_chart_range(start, end, *, period: str):
    end = end or get_working_date()
    if not start:
        if period == "all":
            start = _earliest_dashboard_activity_date()
        else:
            start = end - timedelta(days=364)
    span = (end - start).days
    daily = span <= 31
    group_fmt = "%Y-%m-%d" if daily else "%Y-%m"
    return start, end, group_fmt, daily


def _bucket_key(entry_date: date, *, group_fmt: str) -> str:
    if group_fmt == "%Y-%m-%d":
        return entry_date.strftime("%Y-%m-%d")
    return entry_date.strftime("%Y-%m")


def journal_chart_metrics(*, period: str = "all", start_date=None, end_date=None, journal_data=None):
    """Line chart series aligned with journal KPIs: Total In, Total Out, Net."""
    data = journal_data or get_general_journal(
        period=period, start_date=start_date, end_date=end_date
    )
    range_start = data.get("period_start")
    range_end = data.get("period_end")
    chart_start, chart_end, group_fmt, chart_daily = _journal_chart_range(
        range_start, range_end, period=period
    )

    in_map: dict[str, float] = defaultdict(float)
    out_map: dict[str, float] = defaultdict(float)
    for row in data.get("rows") or []:
        entry_date = row.get("date")
        if not entry_date:
            continue
        if entry_date < chart_start or entry_date > chart_end:
            continue
        bucket = _bucket_key(entry_date, group_fmt=group_fmt)
        in_map[bucket] += float(row.get("amount_in") or 0)
        out_map[bucket] += float(row.get("amount_out") or 0)

    bucket_keys = _iter_chart_bucket_keys(chart_start, chart_end, daily=chart_daily)
    if chart_daily and len(bucket_keys) > 62:
        bucket_keys = bucket_keys[-62:]
    elif period != "all" and not chart_daily and len(bucket_keys) > 24:
        bucket_keys = bucket_keys[-24:]

    chart_labels = []
    chart_in = []
    chart_out = []
    chart_net = []
    for bucket in bucket_keys:
        in_f = in_map.get(bucket, 0.0)
        out_f = out_map.get(bucket, 0.0)
        chart_labels.append(_format_chart_axis_label(bucket, daily=chart_daily))
        chart_in.append(in_f)
        chart_out.append(out_f)
        chart_net.append(in_f - out_f)

    first_active = 0
    for i in range(len(bucket_keys)):
        if chart_in[i] or chart_out[i] or chart_net[i]:
            first_active = i
            break
    if first_active > 0:
        chart_labels = chart_labels[first_active:]
        chart_in = chart_in[first_active:]
        chart_out = chart_out[first_active:]
        chart_net = chart_net[first_active:]

    return {
        "chart": {
            "labels": chart_labels,
            "in": chart_in,
            "out": chart_out,
            "net": chart_net,
            "granularity": "day" if chart_daily else "month",
        },
    }

"""Sales page chart metrics (sale, returns, net, credit over time)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import case, func

from app.extensions import db
from app.models import Sale, SaleReturn
from app.models.sales import PaymentStatus
from app.services.dashboard_service import (
    _format_chart_axis_label,
    _iter_chart_bucket_keys,
    _range_for_filter,
)
from app.utils.working_date import get_working_date


def _earliest_sales_activity_date() -> date:
    today = get_working_date()
    candidates: list[date] = []
    for raw in (
        db.session.query(func.min(Sale.sale_date)).scalar(),
        db.session.query(func.min(SaleReturn.return_date)).scalar(),
    ):
        if raw:
            candidates.append(raw)
    if not candidates:
        return today - timedelta(days=29)
    return min(candidates)


def _sales_chart_range(start, end, *, period: str):
    end = end or get_working_date()
    if not start:
        if period == "all":
            start = _earliest_sales_activity_date()
        else:
            start = end - timedelta(days=364)
    span = (end - start).days
    daily = span <= 31
    group_fmt = "%Y-%m-%d" if daily else "%Y-%m"
    return start, end, group_fmt, daily


def _period_totals(range_start, range_end):
    total_sale_q = db.session.query(func.coalesce(func.sum(Sale.grand_total), 0))
    total_credit_q = db.session.query(
        func.coalesce(
            func.sum(
                case(
                    (Sale.amount_paid < Sale.grand_total, Sale.grand_total - Sale.amount_paid),
                    else_=0,
                )
            ),
            0,
        )
    ).filter(Sale.payment_status != PaymentStatus.PAID)
    total_returns_q = db.session.query(func.coalesce(func.sum(SaleReturn.grand_total), 0))
    if range_start:
        total_sale_q = total_sale_q.filter(Sale.sale_date >= range_start)
        total_credit_q = total_credit_q.filter(Sale.sale_date >= range_start)
        total_returns_q = total_returns_q.filter(SaleReturn.return_date >= range_start)
    if range_end:
        total_sale_q = total_sale_q.filter(Sale.sale_date <= range_end)
        total_credit_q = total_credit_q.filter(Sale.sale_date <= range_end)
        total_returns_q = total_returns_q.filter(SaleReturn.return_date <= range_end)

    total_sale = total_sale_q.scalar() or Decimal("0")
    total_returns = total_returns_q.scalar() or Decimal("0")
    total_credit = total_credit_q.scalar() or Decimal("0")
    net_sale = total_sale - total_returns
    return total_sale, total_returns, total_credit, net_sale


def sales_page_chart_metrics(*, period: str = "all", start_date=None, end_date=None):
    """Line chart aligned with sales page KPIs."""
    range_start, range_end = _range_for_filter(period, start_date, end_date)
    chart_start, chart_end, group_fmt, chart_daily = _sales_chart_range(
        range_start, range_end, period=period
    )

    sales_map = {
        r[0]: float(r[1] or 0)
        for r in db.session.query(
            func.strftime(group_fmt, Sale.sale_date),
            func.coalesce(func.sum(Sale.grand_total), 0),
        )
        .filter(Sale.sale_date >= chart_start, Sale.sale_date <= chart_end)
        .group_by(func.strftime(group_fmt, Sale.sale_date))
        .all()
    }
    returns_map = {
        r[0]: float(r[1] or 0)
        for r in db.session.query(
            func.strftime(group_fmt, SaleReturn.return_date),
            func.coalesce(func.sum(SaleReturn.grand_total), 0),
        )
        .filter(SaleReturn.return_date >= chart_start, SaleReturn.return_date <= chart_end)
        .group_by(func.strftime(group_fmt, SaleReturn.return_date))
        .all()
    }
    credit_expr = case(
        (Sale.amount_paid < Sale.grand_total, Sale.grand_total - Sale.amount_paid),
        else_=0,
    )
    credit_map = {
        r[0]: float(r[1] or 0)
        for r in db.session.query(
            func.strftime(group_fmt, Sale.sale_date),
            func.coalesce(func.sum(credit_expr), 0),
        )
        .filter(
            Sale.sale_date >= chart_start,
            Sale.sale_date <= chart_end,
            Sale.payment_status != PaymentStatus.PAID,
        )
        .group_by(func.strftime(group_fmt, Sale.sale_date))
        .all()
    }

    bucket_keys = _iter_chart_bucket_keys(chart_start, chart_end, daily=chart_daily)
    if chart_daily and len(bucket_keys) > 62:
        bucket_keys = bucket_keys[-62:]
    elif period != "all" and not chart_daily and len(bucket_keys) > 24:
        bucket_keys = bucket_keys[-24:]

    chart_labels = []
    chart_gross = []
    chart_returns = []
    chart_net = []
    chart_credit = []
    for bucket in bucket_keys:
        gross_f = sales_map.get(bucket, 0.0)
        ret_f = returns_map.get(bucket, 0.0)
        net_f = gross_f - ret_f
        credit_f = credit_map.get(bucket, 0.0)
        chart_labels.append(_format_chart_axis_label(bucket, daily=chart_daily))
        chart_gross.append(gross_f)
        chart_returns.append(ret_f)
        chart_net.append(net_f)
        chart_credit.append(credit_f)

    first_active = 0
    for i in range(len(bucket_keys)):
        if chart_gross[i] or chart_returns[i] or chart_net[i] or chart_credit[i]:
            first_active = i
            break
    if first_active > 0:
        chart_labels = chart_labels[first_active:]
        chart_gross = chart_gross[first_active:]
        chart_returns = chart_returns[first_active:]
        chart_net = chart_net[first_active:]
        chart_credit = chart_credit[first_active:]

    total_sale, total_returns, total_credit, net_sale = _period_totals(range_start, range_end)

    return {
        "total_sale": total_sale,
        "total_returns": total_returns,
        "total_credit": total_credit,
        "net_sale": net_sale,
        "chart": {
            "labels": chart_labels,
            "gross": chart_gross,
            "returns": chart_returns,
            "net": chart_net,
            "credit": chart_credit,
            "granularity": "day" if chart_daily else "month",
        },
    }

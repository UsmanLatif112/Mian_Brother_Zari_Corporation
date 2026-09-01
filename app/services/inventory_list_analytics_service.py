"""Inventory list page chart metrics (stock value, purchases, sales over time)."""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func

from app.extensions import db
from app.models import Purchase, PurchaseItem, Sale, SaleItem, StockLayer
from app.services.dashboard_service import (
    _format_chart_axis_label,
    _iter_chart_bucket_keys,
    _range_for_filter,
)
from app.services.fifo_service import stock_valuation_as_of
from app.utils.working_date import get_working_date


def _earliest_inventory_activity_date() -> date:
    today = get_working_date()
    candidates: list[date] = []
    for raw in (
        db.session.query(func.min(Purchase.purchase_date)).scalar(),
        db.session.query(func.min(Sale.sale_date)).scalar(),
        db.session.query(func.min(func.date(StockLayer.received_at))).scalar(),
    ):
        if raw is None:
            continue
        if isinstance(raw, date):
            candidates.append(raw)
        elif hasattr(raw, "date"):
            candidates.append(raw.date())
        else:
            try:
                candidates.append(date.fromisoformat(str(raw)[:10]))
            except ValueError:
                pass
    if not candidates:
        return today - timedelta(days=29)
    return min(candidates)


def _inventory_chart_range(start, end, *, period: str):
    end = end or get_working_date()
    if not start:
        if period == "all":
            start = _earliest_inventory_activity_date()
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


def _product_filter(q, product_ids, product_col):
    if product_ids is None:
        return q
    if not product_ids:
        return q.filter(product_col.in_([]))
    return q.filter(product_col.in_(product_ids))


def _period_totals(range_start, range_end, product_ids=None):
    purchase_q = (
        db.session.query(func.coalesce(func.sum(PurchaseItem.line_total), 0))
        .select_from(Purchase)
        .join(PurchaseItem, PurchaseItem.purchase_id == Purchase.id)
    )
    purchase_q = _product_filter(purchase_q, product_ids, PurchaseItem.product_id)
    if range_start:
        purchase_q = purchase_q.filter(Purchase.purchase_date >= range_start)
    if range_end:
        purchase_q = purchase_q.filter(Purchase.purchase_date <= range_end)
    total_purchases = Decimal(str(purchase_q.scalar() or 0))

    sale_q = (
        db.session.query(func.coalesce(func.sum(SaleItem.line_total), 0))
        .select_from(Sale)
        .join(SaleItem, SaleItem.sale_id == Sale.id)
    )
    sale_q = _product_filter(sale_q, product_ids, SaleItem.product_id)
    if range_start:
        sale_q = sale_q.filter(Sale.sale_date >= range_start)
    if range_end:
        sale_q = sale_q.filter(Sale.sale_date <= range_end)
    total_sales = Decimal(str(sale_q.scalar() or 0))

    purchase_count_q = (
        db.session.query(func.count(func.distinct(Purchase.id)))
        .select_from(Purchase)
        .join(PurchaseItem, PurchaseItem.purchase_id == Purchase.id)
    )
    purchase_count_q = _product_filter(purchase_count_q, product_ids, PurchaseItem.product_id)
    if range_start:
        purchase_count_q = purchase_count_q.filter(Purchase.purchase_date >= range_start)
    if range_end:
        purchase_count_q = purchase_count_q.filter(Purchase.purchase_date <= range_end)
    purchase_count = int(purchase_count_q.scalar() or 0)

    sale_count_q = (
        db.session.query(func.count(func.distinct(Sale.id)))
        .select_from(Sale)
        .join(SaleItem, SaleItem.sale_id == Sale.id)
    )
    sale_count_q = _product_filter(sale_count_q, product_ids, SaleItem.product_id)
    if range_start:
        sale_count_q = sale_count_q.filter(Sale.sale_date >= range_start)
    if range_end:
        sale_count_q = sale_count_q.filter(Sale.sale_date <= range_end)
    sale_count = int(sale_count_q.scalar() or 0)

    return total_purchases, total_sales, purchase_count, sale_count


def inventory_list_chart_metrics(
    *,
    period: str = "all",
    start_date=None,
    end_date=None,
    product_ids=None,
):
    """Line chart for inventory list: stock value, purchases, sales."""
    range_start, range_end = _range_for_filter(period, start_date, end_date)
    chart_start, chart_end, group_fmt, chart_daily = _inventory_chart_range(
        range_start, range_end, period=period
    )

    purchase_q = (
        db.session.query(
            func.strftime(group_fmt, Purchase.purchase_date),
            func.coalesce(func.sum(PurchaseItem.line_total), 0),
        )
        .join(PurchaseItem, PurchaseItem.purchase_id == Purchase.id)
        .filter(Purchase.purchase_date >= chart_start, Purchase.purchase_date <= chart_end)
    )
    purchase_q = _product_filter(purchase_q, product_ids, PurchaseItem.product_id)
    purchase_map = {r[0]: float(r[1] or 0) for r in purchase_q.group_by(
        func.strftime(group_fmt, Purchase.purchase_date)
    ).all()}

    sale_q = (
        db.session.query(
            func.strftime(group_fmt, Sale.sale_date),
            func.coalesce(func.sum(SaleItem.line_total), 0),
        )
        .join(SaleItem, SaleItem.sale_id == Sale.id)
        .filter(Sale.sale_date >= chart_start, Sale.sale_date <= chart_end)
    )
    sale_q = _product_filter(sale_q, product_ids, SaleItem.product_id)
    sale_map = {r[0]: float(r[1] or 0) for r in sale_q.group_by(
        func.strftime(group_fmt, Sale.sale_date)
    ).all()}

    purchase_count_q = (
        db.session.query(
            func.strftime(group_fmt, Purchase.purchase_date),
            func.count(func.distinct(Purchase.id)),
        )
        .join(PurchaseItem, PurchaseItem.purchase_id == Purchase.id)
        .filter(Purchase.purchase_date >= chart_start, Purchase.purchase_date <= chart_end)
    )
    purchase_count_q = _product_filter(purchase_count_q, product_ids, PurchaseItem.product_id)
    count_map = {r[0]: int(r[1] or 0) for r in purchase_count_q.group_by(
        func.strftime(group_fmt, Purchase.purchase_date)
    ).all()}

    bucket_keys = _iter_chart_bucket_keys(chart_start, chart_end, daily=chart_daily)
    if chart_daily and len(bucket_keys) > 62:
        bucket_keys = bucket_keys[-62:]
    elif period != "all" and not chart_daily and len(bucket_keys) > 24:
        bucket_keys = bucket_keys[-24:]

    chart_labels = []
    chart_stock = []
    chart_purchase = []
    chart_sale = []
    chart_count = []
    for bucket in bucket_keys:
        as_of = _bucket_end(bucket, daily=chart_daily, chart_end=chart_end)
        stock_f = float(
            stock_valuation_as_of(
                as_of,
                product_ids=product_ids if product_ids is not None else None,
            )
        )
        purchase_f = purchase_map.get(bucket, 0.0)
        sale_f = sale_map.get(bucket, 0.0)
        chart_labels.append(_format_chart_axis_label(bucket, daily=chart_daily))
        chart_stock.append(stock_f)
        chart_purchase.append(purchase_f)
        chart_sale.append(sale_f)
        chart_count.append(float(count_map.get(bucket, 0)))

    first_active = 0
    for i in range(len(bucket_keys)):
        if chart_purchase[i] or chart_sale[i] or chart_count[i]:
            first_active = i
            break
    if first_active > 0:
        chart_labels = chart_labels[first_active:]
        chart_stock = chart_stock[first_active:]
        chart_purchase = chart_purchase[first_active:]
        chart_sale = chart_sale[first_active:]
        chart_count = chart_count[first_active:]

    total_purchases, total_sales, purchase_count, sale_count = _period_totals(
        range_start, range_end, product_ids
    )

    return {
        "total_purchases": total_purchases,
        "total_sales": total_sales,
        "purchase_count": purchase_count,
        "sale_count": sale_count,
        "chart": {
            "labels": chart_labels,
            "stock": chart_stock,
            "purchase": chart_purchase,
            "sale": chart_sale,
            "count": chart_count,
            "granularity": "day" if chart_daily else "month",
        },
    }

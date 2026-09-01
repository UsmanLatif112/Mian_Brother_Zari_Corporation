"""Per-product sale, purchase, and profit metrics for the inventory detail page."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func

from app.extensions import db
from app.models import (
    InventoryLoss,
    Purchase,
    PurchaseItem,
    Sale,
    SaleItem,
    SaleReturn,
    SaleReturnItem,
    StockLayer,
)
from app.services.dashboard_service import (
    _chart_range_and_granularity,
    _filters,
    _format_chart_axis_label,
    _iter_chart_bucket_keys,
)
from app.utils.working_date import get_working_date


def _earliest_product_activity_date(product_id: int) -> date:
    """First date this product appears in sales, purchases, stock, or losses."""
    today = get_working_date()
    candidates: list[date] = []

    def _collect(raw):
        if raw is None:
            return
        try:
            if isinstance(raw, date):
                candidates.append(raw)
            elif hasattr(raw, "date"):
                candidates.append(raw.date())
        except Exception:
            pass

    for row in (
        db.session.query(func.min(Sale.sale_date))
        .join(SaleItem, SaleItem.sale_id == Sale.id)
        .filter(SaleItem.product_id == product_id)
        .scalar(),
        db.session.query(func.min(SaleReturn.return_date))
        .join(SaleReturnItem, SaleReturnItem.sale_return_id == SaleReturn.id)
        .filter(SaleReturnItem.product_id == product_id)
        .scalar(),
        db.session.query(func.min(Purchase.purchase_date))
        .join(PurchaseItem, PurchaseItem.purchase_id == Purchase.id)
        .filter(PurchaseItem.product_id == product_id)
        .scalar(),
        db.session.query(func.min(InventoryLoss.loss_date))
        .filter(InventoryLoss.product_id == product_id)
        .scalar(),
        db.session.query(func.min(func.date(StockLayer.received_at)))
        .filter(StockLayer.product_id == product_id)
        .scalar(),
    ):
        _collect(row)

    if not candidates:
        return today - timedelta(days=29)
    return min(candidates)


def _product_chart_range(start, end, *, period: str, product_id: int):
    """Like dashboard chart range but anchored to this product's first activity."""
    end = end or get_working_date()
    if not start:
        if period == "all":
            start = _earliest_product_activity_date(product_id)
        else:
            start = end - timedelta(days=364)
    span = (end - start).days
    daily = span <= 31
    group_fmt = "%Y-%m-%d" if daily else "%Y-%m"
    return start, end, group_fmt, daily


def _sum_product_sales(product_id: int, start=None, end=None) -> Decimal:
    q = (
        db.session.query(func.coalesce(func.sum(SaleItem.line_total), 0))
        .join(Sale, Sale.id == SaleItem.sale_id)
        .filter(SaleItem.product_id == product_id)
    )
    for f in _filters(Sale.sale_date, start, end):
        q = q.filter(f)
    gross = q.scalar() or Decimal("0")

    rq = (
        db.session.query(func.coalesce(func.sum(SaleReturnItem.line_total), 0))
        .join(SaleReturn, SaleReturn.id == SaleReturnItem.sale_return_id)
        .filter(SaleReturnItem.product_id == product_id)
    )
    for f in _filters(SaleReturn.return_date, start, end):
        rq = rq.filter(f)
    returns = rq.scalar() or Decimal("0")
    return gross - returns


def _sum_product_cost(product_id: int, start=None, end=None) -> Decimal:
    q = (
        db.session.query(func.coalesce(func.sum(SaleItem.cost_of_goods), 0))
        .join(Sale, Sale.id == SaleItem.sale_id)
        .filter(SaleItem.product_id == product_id)
    )
    for f in _filters(Sale.sale_date, start, end):
        q = q.filter(f)
    gross = q.scalar() or Decimal("0")

    rq = (
        db.session.query(func.coalesce(func.sum(SaleReturnItem.cost_of_goods), 0))
        .join(SaleReturn, SaleReturn.id == SaleReturnItem.sale_return_id)
        .filter(SaleReturnItem.product_id == product_id)
    )
    for f in _filters(SaleReturn.return_date, start, end):
        rq = rq.filter(f)
    returns = rq.scalar() or Decimal("0")
    return gross - returns


def _sum_product_purchase(product_id: int, start=None, end=None) -> Decimal:
    q = (
        db.session.query(func.coalesce(func.sum(PurchaseItem.line_total), 0))
        .join(Purchase, Purchase.id == PurchaseItem.purchase_id)
        .filter(PurchaseItem.product_id == product_id)
    )
    for f in _filters(Purchase.purchase_date, start, end):
        q = q.filter(f)
    return q.scalar() or Decimal("0")


def _sum_product_inventory_loss(product_id: int, start=None, end=None) -> Decimal:
    q = db.session.query(func.coalesce(func.sum(InventoryLoss.amount), 0)).filter(
        InventoryLoss.product_id == product_id
    )
    if start:
        q = q.filter(InventoryLoss.loss_date >= start)
    if end:
        q = q.filter(InventoryLoss.loss_date <= end)
    return q.scalar() or Decimal("0")


def product_performance_metrics(product_id: int, *, period: str = "all", start=None, end=None):
    """Summary totals and chart series for one product (matches dashboard chart style)."""
    total_sale = _sum_product_sales(product_id, start, end)
    total_purchase = _sum_product_purchase(product_id, start, end)
    total_cost = _sum_product_cost(product_id, start, end)
    inventory_loss = _sum_product_inventory_loss(product_id, start, end)
    gross_profit = total_sale - total_cost - inventory_loss

    chart_start, chart_end, group_fmt, chart_daily = _product_chart_range(
        start, end, period=period, product_id=product_id
    )

    sales_map = {
        r[0]: float(r[1] or 0)
        for r in db.session.query(
            func.strftime(group_fmt, Sale.sale_date),
            func.coalesce(func.sum(SaleItem.line_total), 0),
        )
        .join(Sale, Sale.id == SaleItem.sale_id)
        .filter(SaleItem.product_id == product_id, *_filters(Sale.sale_date, chart_start, chart_end))
        .group_by(func.strftime(group_fmt, Sale.sale_date))
        .all()
    }
    returns_map = {
        r[0]: float(r[1] or 0)
        for r in db.session.query(
            func.strftime(group_fmt, SaleReturn.return_date),
            func.coalesce(func.sum(SaleReturnItem.line_total), 0),
        )
        .join(SaleReturn, SaleReturn.id == SaleReturnItem.sale_return_id)
        .filter(
            SaleReturnItem.product_id == product_id,
            *_filters(SaleReturn.return_date, chart_start, chart_end),
        )
        .group_by(func.strftime(group_fmt, SaleReturn.return_date))
        .all()
    }
    purchase_map = {
        r[0]: float(r[1] or 0)
        for r in db.session.query(
            func.strftime(group_fmt, Purchase.purchase_date),
            func.coalesce(func.sum(PurchaseItem.line_total), 0),
        )
        .join(Purchase, Purchase.id == PurchaseItem.purchase_id)
        .filter(
            PurchaseItem.product_id == product_id,
            *_filters(Purchase.purchase_date, chart_start, chart_end),
        )
        .group_by(func.strftime(group_fmt, Purchase.purchase_date))
        .all()
    }
    sales_cost_map = {
        r[0]: float(r[1] or 0)
        for r in db.session.query(
            func.strftime(group_fmt, Sale.sale_date),
            func.coalesce(func.sum(SaleItem.cost_of_goods), 0),
        )
        .join(Sale, Sale.id == SaleItem.sale_id)
        .filter(SaleItem.product_id == product_id, *_filters(Sale.sale_date, chart_start, chart_end))
        .group_by(func.strftime(group_fmt, Sale.sale_date))
        .all()
    }
    returns_cost_map = {
        r[0]: float(r[1] or 0)
        for r in db.session.query(
            func.strftime(group_fmt, SaleReturn.return_date),
            func.coalesce(func.sum(SaleReturnItem.cost_of_goods), 0),
        )
        .join(SaleReturn, SaleReturn.id == SaleReturnItem.sale_return_id)
        .filter(
            SaleReturnItem.product_id == product_id,
            *_filters(SaleReturn.return_date, chart_start, chart_end),
        )
        .group_by(func.strftime(group_fmt, SaleReturn.return_date))
        .all()
    }
    loss_map = {
        r[0]: float(r[1] or 0)
        for r in db.session.query(
            func.strftime(group_fmt, InventoryLoss.loss_date),
            func.coalesce(func.sum(InventoryLoss.amount), 0),
        )
        .filter(
            InventoryLoss.product_id == product_id,
            *_filters(InventoryLoss.loss_date, chart_start, chart_end),
        )
        .group_by(func.strftime(group_fmt, InventoryLoss.loss_date))
        .all()
    }

    bucket_keys = _iter_chart_bucket_keys(chart_start, chart_end, daily=chart_daily)
    if chart_daily and len(bucket_keys) > 62:
        bucket_keys = bucket_keys[-62:]
    elif period != "all" and not chart_daily and len(bucket_keys) > 24:
        bucket_keys = bucket_keys[-24:]

    chart_labels = []
    chart_sale = []
    chart_purchase = []
    chart_cost = []
    chart_loss = []
    chart_gross = []
    for bucket in bucket_keys:
        sale_f = sales_map.get(bucket, 0.0) - returns_map.get(bucket, 0.0)
        purchase_f = purchase_map.get(bucket, 0.0)
        cost_f = sales_cost_map.get(bucket, 0.0) - returns_cost_map.get(bucket, 0.0)
        loss_f = loss_map.get(bucket, 0.0)
        gross_f = sale_f - cost_f - loss_f
        chart_labels.append(_format_chart_axis_label(bucket, daily=chart_daily))
        chart_sale.append(sale_f)
        chart_purchase.append(purchase_f)
        chart_cost.append(cost_f)
        chart_loss.append(loss_f)
        chart_gross.append(gross_f)

    first_active = 0
    for i in range(len(bucket_keys)):
        if (
            chart_sale[i]
            or chart_purchase[i]
            or chart_cost[i]
            or chart_loss[i]
            or chart_gross[i]
        ):
            first_active = i
            break
    if first_active > 0:
        chart_labels = chart_labels[first_active:]
        chart_sale = chart_sale[first_active:]
        chart_purchase = chart_purchase[first_active:]
        chart_cost = chart_cost[first_active:]
        chart_loss = chart_loss[first_active:]
        chart_gross = chart_gross[first_active:]

    return {
        "total_sale": total_sale,
        "total_purchase": total_purchase,
        "total_cost": total_cost,
        "inventory_loss": inventory_loss,
        "gross_profit": gross_profit,
        "chart": {
            "labels": chart_labels,
            "sale": chart_sale,
            "purchase": chart_purchase,
            "cost": chart_cost,
            "loss": chart_loss,
            "gross": chart_gross,
            "granularity": "day" if chart_daily else "month",
        },
    }

"""Inventory profitability by category (and products within a category) for a period."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, or_

from app.extensions import db
from app.models import (
    Category,
    InventoryLoss,
    Product,
    Sale,
    SaleItem,
    SaleReturn,
    SaleReturnItem,
)
from app.services.dashboard_service import (
    _chart_range_and_granularity,
    _filters,
    _format_chart_axis_label,
    _iter_chart_bucket_keys,
    _range_for_filter,
)
from app.utils.working_date import get_working_date


def _d(value) -> Decimal:
    return Decimal(str(value or 0))


def _f(value) -> float:
    return float(_d(value))


def _margin_pct(sale: Decimal, profit: Decimal) -> float:
    if sale <= 0:
        return 0.0
    return float((profit / sale) * Decimal("100"))


def _product_scope(category_id: int | None):
    """Optional filter: products in a category or its subcategory."""
    if not category_id:
        return []
    return [
        or_(
            Product.category_id == category_id,
            Product.subcategory_id == category_id,
        )
    ]


def _group_key_col(category_id: int | None):
    """
    No filter → roll up by top-level category.
    With filter → roll up by product (drill-down inside the chosen category).
    """
    if category_id:
        return Product.id, "product"
    return Product.category_id, "category"


def _sum_by_group(amount_col, date_col, join_model, item_model, fk_col, start, end, category_id):
    """Sum amount_col grouped by category or product for sale/return lines."""
    group_col, _ = _group_key_col(category_id)
    q = (
        db.session.query(
            group_col.label("gid"),
            func.coalesce(func.sum(amount_col), 0).label("total"),
        )
        .select_from(item_model)
        .join(join_model, join_model.id == fk_col)
        .join(Product, Product.id == item_model.product_id)
        .filter(Product.is_deleted.is_(False))
    )
    for f in _filters(date_col, start, end):
        q = q.filter(f)
    for f in _product_scope(category_id):
        q = q.filter(f)
    return {r.gid: _d(r.total) for r in q.group_by(group_col).all() if r.gid is not None}


def _loss_by_group(start, end, category_id):
    group_col, _ = _group_key_col(category_id)
    q = (
        db.session.query(
            group_col.label("gid"),
            func.coalesce(func.sum(InventoryLoss.amount), 0).label("total"),
        )
        .join(Product, Product.id == InventoryLoss.product_id)
        .filter(Product.is_deleted.is_(False))
    )
    for f in _filters(InventoryLoss.loss_date, start, end):
        q = q.filter(f)
    for f in _product_scope(category_id):
        q = q.filter(f)
    return {r.gid: _d(r.total) for r in q.group_by(group_col).all() if r.gid is not None}


def _qty_sold_by_group(start, end, category_id):
    group_col, _ = _group_key_col(category_id)
    q = (
        db.session.query(
            group_col.label("gid"),
            func.coalesce(func.sum(SaleItem.quantity), 0).label("qty"),
        )
        .join(Sale, Sale.id == SaleItem.sale_id)
        .join(Product, Product.id == SaleItem.product_id)
        .filter(Product.is_deleted.is_(False))
    )
    for f in _filters(Sale.sale_date, start, end):
        q = q.filter(f)
    for f in _product_scope(category_id):
        q = q.filter(f)
    sales = {r.gid: _d(r.qty) for r in q.group_by(group_col).all() if r.gid is not None}

    rq = (
        db.session.query(
            group_col.label("gid"),
            func.coalesce(func.sum(SaleReturnItem.quantity), 0).label("qty"),
        )
        .join(SaleReturn, SaleReturn.id == SaleReturnItem.sale_return_id)
        .join(Product, Product.id == SaleReturnItem.product_id)
        .filter(Product.is_deleted.is_(False))
    )
    for f in _filters(SaleReturn.return_date, start, end):
        rq = rq.filter(f)
    for f in _product_scope(category_id):
        rq = rq.filter(f)
    for r in rq.group_by(group_col).all():
        if r.gid is None:
            continue
        sales[r.gid] = sales.get(r.gid, Decimal("0")) - _d(r.qty)
    return sales


def _name_map(ids: list[int], mode: str) -> dict[int, str]:
    if not ids:
        return {}
    if mode == "product":
        return {
            p.id: p.name
            for p in Product.query.filter(Product.id.in_(ids)).all()
        }
    return {
        c.id: c.name
        for c in Category.query.filter(Category.id.in_(ids)).all()
    }


def _url_for_row(gid: int, mode: str) -> str | None:
    from flask import url_for

    try:
        if mode == "product":
            return url_for("inventory.detail", product_id=gid)
        return url_for("categories.detail", category_id=gid)
    except Exception:
        return None


def _bucket_series(start, end, period: str, category_id: int | None):
    chart_start, chart_end, group_fmt, chart_daily = _chart_range_and_granularity(
        start, end, period=period
    )
    scope = _product_scope(category_id)

    def _map(date_col, amount_col, join_model, item_model, fk_col):
        q = (
            db.session.query(
                func.strftime(group_fmt, date_col).label("bucket"),
                func.coalesce(func.sum(amount_col), 0).label("total"),
            )
            .select_from(item_model)
            .join(join_model, join_model.id == fk_col)
            .join(Product, Product.id == item_model.product_id)
            .filter(Product.is_deleted.is_(False), *_filters(date_col, chart_start, chart_end), *scope)
            .group_by(func.strftime(group_fmt, date_col))
        )
        return {r.bucket: _f(r.total) for r in q.all()}

    sales_map = _map(Sale.sale_date, SaleItem.line_total, Sale, SaleItem, SaleItem.sale_id)
    returns_map = _map(
        SaleReturn.return_date,
        SaleReturnItem.line_total,
        SaleReturn,
        SaleReturnItem,
        SaleReturnItem.sale_return_id,
    )
    cost_map = _map(Sale.sale_date, SaleItem.cost_of_goods, Sale, SaleItem, SaleItem.sale_id)
    ret_cost_map = _map(
        SaleReturn.return_date,
        SaleReturnItem.cost_of_goods,
        SaleReturn,
        SaleReturnItem,
        SaleReturnItem.sale_return_id,
    )

    loss_q = (
        db.session.query(
            func.strftime(group_fmt, InventoryLoss.loss_date).label("bucket"),
            func.coalesce(func.sum(InventoryLoss.amount), 0).label("total"),
        )
        .join(Product, Product.id == InventoryLoss.product_id)
        .filter(
            Product.is_deleted.is_(False),
            *_filters(InventoryLoss.loss_date, chart_start, chart_end),
            *scope,
        )
        .group_by(func.strftime(group_fmt, InventoryLoss.loss_date))
    )
    loss_map = {r.bucket: _f(r.total) for r in loss_q.all()}

    bucket_keys = _iter_chart_bucket_keys(chart_start, chart_end, daily=chart_daily)
    if chart_daily and len(bucket_keys) > 62:
        bucket_keys = bucket_keys[-62:]
    elif period != "all" and not chart_daily and len(bucket_keys) > 24:
        bucket_keys = bucket_keys[-24:]

    labels, sale_s, cost_s, loss_s, gross_s = [], [], [], [], []
    for bucket in bucket_keys:
        sale_f = sales_map.get(bucket, 0.0) - returns_map.get(bucket, 0.0)
        cost_f = cost_map.get(bucket, 0.0) - ret_cost_map.get(bucket, 0.0)
        loss_f = loss_map.get(bucket, 0.0)
        labels.append(_format_chart_axis_label(bucket, daily=chart_daily))
        sale_s.append(sale_f)
        cost_s.append(cost_f)
        loss_s.append(loss_f)
        gross_s.append(sale_f - cost_f - loss_f)

    first_active = 0
    for i in range(len(bucket_keys)):
        if sale_s[i] or cost_s[i] or loss_s[i] or gross_s[i]:
            first_active = i
            break
    if first_active > 0:
        labels = labels[first_active:]
        sale_s = sale_s[first_active:]
        cost_s = cost_s[first_active:]
        loss_s = loss_s[first_active:]
        gross_s = gross_s[first_active:]

    return {
        "labels": labels,
        "sale": sale_s,
        "cost": cost_s,
        "loss": loss_s,
        "gross": gross_s,
        "granularity": "day" if chart_daily else "month",
    }


def inventory_profitability_metrics(
    *,
    period: str = "all",
    start_date: date | None = None,
    end_date: date | None = None,
    category_id: int | None = None,
):
    """Category (or product) profitability for the selected period."""
    start, end = _range_for_filter(period, start_date, end_date)
    mode = "product" if category_id else "category"

    sale_map = _sum_by_group(
        SaleItem.line_total, Sale.sale_date, Sale, SaleItem, SaleItem.sale_id, start, end, category_id
    )
    sale_ret_map = _sum_by_group(
        SaleReturnItem.line_total,
        SaleReturn.return_date,
        SaleReturn,
        SaleReturnItem,
        SaleReturnItem.sale_return_id,
        start,
        end,
        category_id,
    )
    cost_map = _sum_by_group(
        SaleItem.cost_of_goods, Sale.sale_date, Sale, SaleItem, SaleItem.sale_id, start, end, category_id
    )
    cost_ret_map = _sum_by_group(
        SaleReturnItem.cost_of_goods,
        SaleReturn.return_date,
        SaleReturn,
        SaleReturnItem,
        SaleReturnItem.sale_return_id,
        start,
        end,
        category_id,
    )
    loss_map = _loss_by_group(start, end, category_id)
    qty_map = _qty_sold_by_group(start, end, category_id)

    all_ids = set(sale_map) | set(sale_ret_map) | set(cost_map) | set(cost_ret_map) | set(loss_map)
    names = _name_map(list(all_ids), mode)

    rows = []
    total_sale = Decimal("0")
    total_cost = Decimal("0")
    total_loss = Decimal("0")
    total_profit = Decimal("0")

    for gid in all_ids:
        sale = sale_map.get(gid, Decimal("0")) - sale_ret_map.get(gid, Decimal("0"))
        cost = cost_map.get(gid, Decimal("0")) - cost_ret_map.get(gid, Decimal("0"))
        loss = loss_map.get(gid, Decimal("0"))
        profit = sale - cost - loss
        qty = qty_map.get(gid, Decimal("0"))
        total_sale += sale
        total_cost += cost
        total_loss += loss
        total_profit += profit
        rows.append(
            {
                "id": gid,
                "name": names.get(gid, f"{'Product' if mode == 'product' else 'Category'} #{gid}"),
                "sale": _f(sale),
                "cost": _f(cost),
                "loss": _f(loss),
                "profit": _f(profit),
                "margin_pct": _margin_pct(sale, profit),
                "qty": _f(qty),
                "url": _url_for_row(gid, mode),
            }
        )

    rows.sort(key=lambda r: r["profit"], reverse=True)

    # Charts: profit share pie + comparison bar (top 12 for readability)
    chart_rows = [r for r in rows if abs(r["profit"]) > 0.0001][:12]
    if not chart_rows and rows:
        chart_rows = rows[:12]

    pie_rows = [
        {
            "id": r["id"],
            "name": r["name"],
            "value": max(r["profit"], 0.0),
            "detail": f"Margin {r['margin_pct']:.1f}% · Sale {r['sale']:,.2f}",
        }
        for r in rows
        if r["profit"] > 0
    ][:8]

    most_profitable = rows[0] if rows and rows[0]["profit"] > 0 else None
    least_profitable = None
    if len(rows) > 1:
        candidate = min(rows, key=lambda r: r["profit"])
        if not most_profitable or candidate["id"] != most_profitable["id"]:
            least_profitable = candidate
    elif rows and rows[0]["profit"] < 0:
        least_profitable = rows[0]

    return {
        "mode": mode,
        "period": period,
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "period_as_of": (end or get_working_date()).isoformat(),
        "total_sale": total_sale,
        "total_cost": total_cost,
        "inventory_loss": total_loss,
        "gross_profit": total_profit,
        "margin_pct": _margin_pct(total_sale, total_profit),
        "row_count": len(rows),
        "most_profitable": most_profitable,
        "least_profitable": least_profitable,
        "rows": rows,
        "trend": _bucket_series(start, end, period, category_id),
        "category_chart": {
            "labels": [r["name"] for r in chart_rows],
            "profit": [r["profit"] for r in chart_rows],
            "sale": [r["sale"] for r in chart_rows],
            "cost": [r["cost"] for r in chart_rows],
            "margin": [r["margin_pct"] for r in chart_rows],
        },
        "pie": {
            "labels": [r["name"] for r in pie_rows],
            "values": [r["value"] for r in pie_rows],
            "rows": pie_rows,
        },
    }

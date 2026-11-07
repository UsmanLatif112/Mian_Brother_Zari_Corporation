"""Vendor list page chart metrics (purchase, paid, payable over time)."""

from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal

from sqlalchemy import func

from app.extensions import db
from app.models import (
    Purchase,
    PurchaseItem,
    Sale,
    SaleItem,
    SaleReturn,
    SaleReturnItem,
    StockLayer,
)
from app.services.dashboard_service import (
    _filters,
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


def vendor_gross_profit_map(*, period: str = "all", start_date=None, end_date=None, vendor_ids=None):
    """
    Gross profit per vendor for the period, from sales of products primarily
    supplied by that vendor (sale − FIFO COGS, net of returns).
    """
    start, end = _range_for_filter(period, start_date, end_date)
    if vendor_ids is not None and not vendor_ids:
        return {}

    # Primary vendor per product (most stock received; purchases as fallback)
    layer_rows = (
        db.session.query(
            StockLayer.product_id,
            StockLayer.vendor_id,
            func.coalesce(func.sum(StockLayer.quantity_received), 0).label("qty"),
            func.max(StockLayer.received_at).label("latest"),
        )
        .filter(StockLayer.vendor_id.isnot(None))
        .group_by(StockLayer.product_id, StockLayer.vendor_id)
        .all()
    )
    best: dict[int, tuple[Decimal, object, int]] = {}
    for product_id, vendor_id, qty, latest in layer_rows:
        if product_id is None or vendor_id is None:
            continue
        qty_d = Decimal(str(qty or 0))
        cur = best.get(product_id)
        if cur is None or qty_d > cur[0] or (qty_d == cur[0] and (latest or "") > (cur[1] or "")):
            best[product_id] = (qty_d, latest, int(vendor_id))

    product_vendor = {pid: vid for pid, (_, __, vid) in best.items()}

    purchase_rows = (
        db.session.query(
            PurchaseItem.product_id,
            Purchase.vendor_id,
            func.coalesce(func.sum(PurchaseItem.quantity), 0).label("qty"),
            func.max(Purchase.purchase_date).label("latest"),
        )
        .join(Purchase, Purchase.id == PurchaseItem.purchase_id)
        .group_by(PurchaseItem.product_id, Purchase.vendor_id)
        .all()
    )
    purchase_best: dict[int, tuple[Decimal, object, int]] = {}
    for product_id, vendor_id, qty, latest in purchase_rows:
        if product_id is None or vendor_id is None or product_id in product_vendor:
            continue
        qty_d = Decimal(str(qty or 0))
        cur = purchase_best.get(product_id)
        if cur is None or qty_d > cur[0] or (qty_d == cur[0] and (latest or "") > (cur[1] or "")):
            purchase_best[product_id] = (qty_d, latest, int(vendor_id))
    product_vendor.update({pid: vid for pid, (_, __, vid) in purchase_best.items()})

    if not product_vendor:
        return {}

    if vendor_ids is not None:
        allowed = set(vendor_ids)
        product_vendor = {pid: vid for pid, vid in product_vendor.items() if vid in allowed}
        if not product_vendor:
            return {}

    product_ids = list(product_vendor.keys())

    profit_expr = SaleItem.line_total - SaleItem.cost_of_goods
    sales_q = (
        db.session.query(
            SaleItem.product_id,
            func.coalesce(func.sum(profit_expr), 0),
        )
        .join(Sale, Sale.id == SaleItem.sale_id)
        .filter(SaleItem.product_id.in_(product_ids))
    )
    for f in _filters(Sale.sale_date, start, end):
        sales_q = sales_q.filter(f)
    product_profit = {
        pid: Decimal(str(total or 0))
        for pid, total in sales_q.group_by(SaleItem.product_id).all()
    }

    ret_expr = SaleReturnItem.line_total - SaleReturnItem.cost_of_goods
    ret_q = (
        db.session.query(
            SaleReturnItem.product_id,
            func.coalesce(func.sum(ret_expr), 0),
        )
        .join(SaleReturn, SaleReturn.id == SaleReturnItem.sale_return_id)
        .filter(SaleReturnItem.product_id.in_(product_ids))
    )
    for f in _filters(SaleReturn.return_date, start, end):
        ret_q = ret_q.filter(f)
    for pid, total in ret_q.group_by(SaleReturnItem.product_id).all():
        product_profit[pid] = product_profit.get(pid, Decimal("0")) - Decimal(str(total or 0))

    merged: dict[int, Decimal] = {}
    for pid, profit in product_profit.items():
        vid = product_vendor.get(pid)
        if vid is None:
            continue
        merged[vid] = merged.get(vid, Decimal("0")) + profit
    return merged

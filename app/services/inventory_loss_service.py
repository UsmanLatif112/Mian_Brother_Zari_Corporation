"""Non-cash inventory losses and sale backorder (negative stock) fulfillment."""

from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func

from app.extensions import db
from app.models import InventoryLoss, SaleItem, SaleItemBackorder
from app.models.mixins import utcnow
from app.utils.working_date import get_working_date


def _d(value) -> Decimal:
    return Decimal(str(value or 0))


def _money(value) -> Decimal:
    return _d(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def open_backorder_qty(product_id: int) -> Decimal:
    total = (
        db.session.query(
            func.coalesce(
                func.sum(SaleItemBackorder.qty_backordered - SaleItemBackorder.qty_fulfilled),
                0,
            )
        )
        .filter(
            SaleItemBackorder.product_id == product_id,
            SaleItemBackorder.status.in_(("open", "partial")),
        )
        .scalar()
    )
    return _d(total)


def list_open_backorders(product_id: int):
    """Open/partial backorders for a product (pending stock to cover)."""
    rows = (
        SaleItemBackorder.query.filter(
            SaleItemBackorder.product_id == int(product_id),
            SaleItemBackorder.status.in_(("open", "partial")),
        )
        .order_by(SaleItemBackorder.id.asc())
        .all()
    )
    out = []
    for bo in rows:
        remaining = _d(bo.qty_backordered) - _d(bo.qty_fulfilled)
        if remaining <= 0:
            continue
        w_rem = None
        if bo.weight_backordered is not None:
            w_rem = _d(bo.weight_backordered) - _d(bo.weight_fulfilled)
            if w_rem < 0:
                w_rem = Decimal("0")
        sale_item = bo.sale_item
        sale = sale_item.sale if sale_item else None
        out.append(
            {
                "id": bo.id,
                "qty_remaining": remaining,
                "weight_remaining": w_rem,
                "status": bo.status,
                "sale_item_id": bo.sale_item_id,
                "invoice_no": sale.invoice_no if sale else None,
                "sale_id": sale.id if sale else None,
                "sale_date": sale.sale_date if sale else None,
            }
        )
    return out


def record_inventory_loss(
    *,
    product_id,
    quantity,
    unit_cost,
    source_type,
    source_id=None,
    loss_date=None,
    notes=None,
    user_id=None,
):
    qty = _d(quantity)
    cost = _d(unit_cost)
    if qty <= 0 or cost < 0:
        return None
    amount = _money(qty * cost)
    if amount <= 0:
        return None
    row = InventoryLoss(
        loss_date=loss_date or get_working_date(),
        product_id=int(product_id),
        source_type=source_type,
        source_id=source_id,
        quantity=qty,
        unit_cost=cost,
        amount=amount,
        notes=notes,
        created_by_id=user_id,
        created_at=utcnow(),
    )
    db.session.add(row)
    return row


def create_sale_backorder(
    *,
    sale_item_id,
    product_id,
    qty_backordered,
    weight_backordered=None,
):
    qty = _d(qty_backordered)
    if qty <= 0:
        return None
    row = SaleItemBackorder(
        sale_item_id=int(sale_item_id),
        product_id=int(product_id),
        qty_backordered=qty,
        qty_fulfilled=Decimal("0"),
        weight_backordered=_d(weight_backordered) if weight_backordered is not None else None,
        weight_fulfilled=Decimal("0") if weight_backordered is not None else None,
        status="open",
        created_at=utcnow(),
    )
    db.session.add(row)
    return row


def _consume_layers_qty(product, qty_needed):
    """Reduce sealed bag qty on FIFO layers (used when covering backorders)."""
    from app.models import StockLayer

    need = _d(qty_needed)
    if need <= 0:
        return
    layers = (
        StockLayer.query.filter_by(product_id=product.id)
        .order_by(StockLayer.received_at.asc(), StockLayer.id.asc())
        .all()
    )
    for layer in layers:
        if need <= 0:
            break
        sealed = _d(layer.quantity_remaining)
        if sealed <= 0:
            continue
        take = min(sealed, need)
        layer.quantity_remaining = sealed - take
        need -= take


def fulfill_backorders_for_product(product, available_qty, unit_cost, user_id=None):
    """
    Cover open backorders with newly received stock (FIFO by backorder id).

    Deducts covered qty from layers so physical stock stays correct, and
    true-ups SaleItem.cost_of_goods for the covered portion.

    Returns qty still available after covering backorders.
    """
    avail = _d(available_qty)
    if avail <= 0:
        return avail
    cost = _d(unit_cost)

    rows = (
        SaleItemBackorder.query.filter(
            SaleItemBackorder.product_id == product.id,
            SaleItemBackorder.status.in_(("open", "partial")),
        )
        .order_by(SaleItemBackorder.id.asc())
        .all()
    )
    covered_total = Decimal("0")
    for bo in rows:
        if avail <= 0:
            break
        remaining = _d(bo.qty_backordered) - _d(bo.qty_fulfilled)
        if remaining <= 0:
            bo.status = "closed"
            continue
        take = min(avail, remaining)
        bo.qty_fulfilled = _d(bo.qty_fulfilled) + take
        if bo.weight_backordered is not None and _d(bo.qty_backordered) > 0:
            w_ratio = take / _d(bo.qty_backordered)
            bo.weight_fulfilled = _d(bo.weight_fulfilled) + (
                _d(bo.weight_backordered) * w_ratio
            ).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
        left = _d(bo.qty_backordered) - _d(bo.qty_fulfilled)
        bo.status = "closed" if left <= Decimal("0.000001") else "partial"
        bo.updated_at = utcnow()

        item = db.session.get(SaleItem, bo.sale_item_id)
        if item:
            item.cost_of_goods = _money(_d(item.cost_of_goods) + take * cost)

        avail -= take
        covered_total += take

    if covered_total > 0:
        _consume_layers_qty(product, covered_total)

    return avail


def period_inventory_loss(start=None, end=None) -> Decimal:
    q = db.session.query(func.coalesce(func.sum(InventoryLoss.amount), 0))
    if start:
        q = q.filter(InventoryLoss.loss_date >= start)
    if end:
        q = q.filter(InventoryLoss.loss_date <= end)
    return _d(q.scalar())

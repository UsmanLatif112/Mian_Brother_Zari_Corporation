from decimal import Decimal

from app.extensions import db
from app.models import InventoryAdjustment, StockLayer, StockMovement
from app.models.mixins import utcnow


def add_stock_layer(
    product_id,
    quantity,
    unit_cost,
    source_type,
    source_id=None,
    sale_price=None,
    notes=None,
):
    layer = StockLayer(
        product_id=product_id,
        quantity_remaining=Decimal(str(quantity)),
        unit_cost=Decimal(str(unit_cost)),
        sale_price=Decimal(str(sale_price)) if sale_price is not None else None,
        source_type=source_type,
        source_id=source_id,
        received_at=utcnow(),
        notes=notes,
    )
    db.session.add(layer)
    return layer


def fifo_deduct(product, quantity, movement_type, reference_type, reference_id, user_id, notes=None):
    qty_needed = Decimal(str(quantity))
    total_cost = Decimal("0")
    layers = (
        StockLayer.query.filter_by(product_id=product.id)
        .filter(StockLayer.quantity_remaining > 0)
        .order_by(StockLayer.received_at.asc(), StockLayer.id.asc())
        .all()
    )
    for layer in layers:
        if qty_needed <= 0:
            break
        take = min(layer.quantity_remaining, qty_needed)
        layer.quantity_remaining -= take
        total_cost += take * layer.unit_cost
        qty_needed -= take
    if qty_needed > 0:
        raise ValueError(f"Insufficient stock for {product.name}")
    product.current_stock -= Decimal(str(quantity))
    movement = StockMovement(
        product_id=product.id,
        movement_type=movement_type,
        quantity=-Decimal(str(quantity)),
        unit_cost=total_cost / Decimal(str(quantity)) if quantity else Decimal("0"),
        balance_after=product.current_stock,
        reference_type=reference_type,
        reference_id=reference_id,
        notes=notes,
        created_by_id=user_id,
        created_at=utcnow(),
    )
    db.session.add(movement)
    return total_cost


def fifo_receive(
    product,
    quantity,
    unit_cost,
    source_type,
    source_id,
    user_id,
    notes=None,
    sale_price=None,
):
    qty = Decimal(str(quantity))
    product.current_stock += qty
    # Keep product list prices as latest defaults for display
    product.purchase_price = Decimal(str(unit_cost))
    if sale_price is not None:
        product.sale_price = Decimal(str(sale_price))
    elif product.sale_price is None:
        product.sale_price = Decimal("0")

    layer_sale = sale_price if sale_price is not None else product.sale_price
    add_stock_layer(
        product.id,
        qty,
        unit_cost,
        source_type,
        source_id,
        sale_price=layer_sale,
        notes=notes,
    )
    movement = StockMovement(
        product_id=product.id,
        movement_type="purchase_in",
        quantity=qty,
        unit_cost=Decimal(str(unit_cost)),
        balance_after=product.current_stock,
        reference_type=source_type,
        reference_id=source_id,
        notes=notes,
        created_by_id=user_id,
        created_at=utcnow(),
    )
    db.session.add(movement)


def next_fifo_sale_price(product):
    """Sale price from the oldest remaining batch (FIFO), else product default."""
    layer = (
        StockLayer.query.filter_by(product_id=product.id)
        .filter(StockLayer.quantity_remaining > 0)
        .order_by(StockLayer.received_at.asc(), StockLayer.id.asc())
        .first()
    )
    if layer and layer.sale_price is not None:
        return Decimal(str(layer.sale_price))
    return Decimal(str(product.sale_price or 0))


def adjust_batch(layer, new_qty, user_id, notes=None):
    """Set remaining qty on a batch; sync product.current_stock. Does not touch sold history."""
    layer = db.session.get(StockLayer, layer.id if hasattr(layer, "id") else int(layer))
    if not layer:
        raise ValueError("Batch not found.")
    product = layer.product
    old_qty = Decimal(str(layer.quantity_remaining or 0))
    new_qty = Decimal(str(new_qty))
    if new_qty < 0:
        raise ValueError("Quantity cannot be negative.")
    delta = new_qty - old_qty
    if delta == 0:
        return layer

    layer.quantity_remaining = new_qty
    product.current_stock = Decimal(str(product.current_stock or 0)) + delta

    adj_type = "increase" if delta > 0 else "decrease"
    db.session.add(
        InventoryAdjustment(
            product_id=product.id,
            adjustment_type=adj_type,
            quantity=abs(delta),
            notes=notes or f"Batch #{layer.id} adjust {old_qty} → {new_qty}",
            created_by_id=user_id,
            created_at=utcnow(),
        )
    )
    db.session.add(
        StockMovement(
            product_id=product.id,
            movement_type=f"adjust_{adj_type}",
            quantity=delta,
            unit_cost=layer.unit_cost,
            balance_after=product.current_stock,
            reference_type="stock_layer",
            reference_id=layer.id,
            notes=notes,
            created_by_id=user_id,
            created_at=utcnow(),
        )
    )
    return layer


def reprice_batch(layer, unit_cost=None, sale_price=None, user_id=None, notes=None):
    """
    Update purchase/sale price on remaining stock in this batch only.
    Previously sold quantities are already removed from the layer — not affected.
    """
    layer = db.session.get(StockLayer, layer.id if hasattr(layer, "id") else int(layer))
    if not layer:
        raise ValueError("Batch not found.")
    if Decimal(str(layer.quantity_remaining or 0)) <= 0:
        raise ValueError("No remaining stock in this batch to reprice.")

    changes = []
    if unit_cost is not None:
        old = layer.unit_cost
        layer.unit_cost = Decimal(str(unit_cost))
        changes.append(f"cost {old}→{layer.unit_cost}")
    if sale_price is not None:
        old = layer.sale_price
        layer.sale_price = Decimal(str(sale_price))
        changes.append(f"sale {old}→{layer.sale_price}")

    if not changes:
        return layer

    db.session.add(
        StockMovement(
            product_id=layer.product_id,
            movement_type="reprice",
            quantity=Decimal("0"),
            unit_cost=layer.unit_cost,
            balance_after=layer.product.current_stock,
            reference_type="stock_layer",
            reference_id=layer.id,
            notes=notes or ("Reprice remaining: " + ", ".join(changes)),
            created_by_id=user_id,
            created_at=utcnow(),
        )
    )
    return layer


def reprice_all_remaining(product, unit_cost=None, sale_price=None, user_id=None, notes=None):
    """Reprice every open batch for a product (remaining stock only)."""
    layers = (
        StockLayer.query.filter_by(product_id=product.id)
        .filter(StockLayer.quantity_remaining > 0)
        .all()
    )
    if not layers:
        raise ValueError("No remaining stock to reprice.")
    for layer in layers:
        reprice_batch(layer, unit_cost=unit_cost, sale_price=sale_price, user_id=user_id, notes=notes)
    if sale_price is not None:
        product.sale_price = Decimal(str(sale_price))
    if unit_cost is not None:
        product.purchase_price = Decimal(str(unit_cost))
    return len(layers)


def stock_valuation():
    from app.models import Product

    total = Decimal("0")
    products = Product.query.filter_by(is_deleted=False).all()
    for product in products:
        remaining = product.current_stock
        layers = (
            StockLayer.query.filter_by(product_id=product.id)
            .filter(StockLayer.quantity_remaining > 0)
            .order_by(StockLayer.received_at.asc())
            .all()
        )
        for layer in layers:
            if remaining <= 0:
                break
            take = min(layer.quantity_remaining, remaining)
            total += take * layer.unit_cost
            remaining -= take
    return total

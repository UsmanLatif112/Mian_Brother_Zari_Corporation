from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func

from app.extensions import db
from app.models import InventoryAdjustment, StockLayer, StockMovement
from app.utils.working_date import as_working_datetime, get_working_datetime


def next_product_batch_seq(product_id) -> int:
    """Next batch sequence number for a product (1-based, per product)."""
    if not product_id:
        return 1
    count = (
        db.session.query(func.count(StockLayer.id))
        .filter(StockLayer.product_id == int(product_id))
        .scalar()
        or 0
    )
    return int(count) + 1


def layer_packaging(layer, product=None):
    """
    Packaging for stock math: one product = one unit weight.
    Always prefer the product's unit_weight (ignore per-batch size differences).
    """
    product = product or getattr(layer, "product", None)
    if product is not None and product.unit_weight is not None and Decimal(str(product.unit_weight or 0)) > 0:
        return Decimal(str(product.unit_weight)), (product.weight_unit or "kg")
    if layer is not None and layer.unit_weight is not None and Decimal(str(layer.unit_weight)) > 0:
        return Decimal(str(layer.unit_weight)), (layer.weight_unit or "kg")
    return None, None


def normalize_unit_weight(unit_weight):
    """Normalize packaging weight for comparisons (e.g. 20 vs 20.000)."""
    if unit_weight is None or str(unit_weight).strip() == "":
        return None
    try:
        value = Decimal(str(unit_weight))
    except Exception:
        return None
    if value <= 0:
        return None
    return value.quantize(Decimal("0.001"))


def _ordered_stock_layers(product):
    return (
        StockLayer.query.filter_by(product_id=product.id)
        .order_by(StockLayer.received_at.asc(), StockLayer.id.asc())
        .all()
    )


def list_packaging_options(product):
    """Single packaging option from the product (one weight per product)."""
    from app.utils.weight_utils import format_qty_display

    uw = None
    if product.unit_weight is not None and Decimal(str(product.unit_weight or 0)) > 0:
        uw = Decimal(str(product.unit_weight))
    wu = product.weight_unit or "kg"
    price = float(next_fifo_sale_price(product))
    stock_qty = Decimal(str(product.current_stock or 0))
    return [
        {
            "unit_weight": float(uw) if uw is not None else None,
            "weight_unit": wu if uw else "",
            "sale_price": price,
            "stock_qty": float(stock_qty),
            "stock_display": format_qty_display(stock_qty, uw, wu) if uw else format_qty_display(stock_qty, None, None),
            "label": (
                f"{uw:g} {wu} · {price:.2f}"
                if uw
                else f"Units · {price:.2f}"
            ),
        }
    ]


def next_fifo_packaging(product, unit_weight=None):
    """Product packaging (one weight). unit_weight arg ignored for compatibility."""
    if product.unit_weight is not None and Decimal(str(product.unit_weight or 0)) > 0:
        return Decimal(str(product.unit_weight)), product.weight_unit or "kg"
    return None, None


def _layer_has_stock(layer) -> bool:
    if Decimal(str(layer.quantity_remaining or 0)) > 0:
        return True
    return Decimal(str(getattr(layer, "open_weight_remaining", None) or 0)) > 0


def _layer_available_weight(layer, product) -> Decimal:
    """Total kg/L available on a batch (open leftover + sealed bags × unit weight)."""
    uw, _wu = layer_packaging(layer, product)
    if not uw or uw <= 0:
        return Decimal("0")
    sealed = Decimal(str(layer.quantity_remaining or 0))
    open_w = Decimal(str(getattr(layer, "open_weight_remaining", None) or 0))
    return open_w + sealed * uw


def sync_product_stock(product):
    """Rebuild product.current_stock from sealed bags + open weight."""
    layers = StockLayer.query.filter_by(product_id=product.id).all()
    total = Decimal("0")
    for layer in layers:
        total += layer.stock_qty_equivalent
    product.current_stock = total
    return total


def _layers_for_open_sale(product, unit_weight=None):
    """All stock layers for this product in FIFO order (one product weight)."""
    return list(_ordered_stock_layers(product))


def suggest_open_sale_packaging(product, sale_weight=None):
    """Product unit weight (one weight per product)."""
    return next_fifo_packaging(product)


def weight_to_stock_qty_fifo(product, sale_weight, unit_weight=None) -> Decimal:
    """
    Bag-equivalent qty for an open-weight sale (does not mutate stock).

    Uses open leftover kg first across batches, then sealed bags (FIFO).
    One product = one unit weight.
    """
    remaining_w = Decimal(str(sale_weight or 0))
    if remaining_w <= 0:
        raise ValueError("Sale weight must be greater than zero.")
    uw, _wu = next_fifo_packaging(product)
    if not uw or uw <= 0:
        raise ValueError(f"Unit weight missing for {product.name}.")
    total_qty = Decimal("0")
    layers = [layer for layer in _ordered_stock_layers(product) if _layer_has_stock(layer)]

    for layer in layers:
        if remaining_w <= 0:
            break
        open_w = Decimal(str(getattr(layer, "open_weight_remaining", None) or 0))
        if open_w <= 0:
            continue
        take_w = min(remaining_w, open_w)
        total_qty += take_w / uw
        remaining_w -= take_w

    for layer in layers:
        if remaining_w <= 0:
            break
        sealed = Decimal(str(layer.quantity_remaining or 0))
        if sealed <= 0:
            continue
        avail_w = sealed * uw
        take_w = min(remaining_w, avail_w)
        total_qty += take_w / uw
        remaining_w -= take_w

    if remaining_w > Decimal("0.000001"):
        raise ValueError(f"Insufficient stock weight for {product.name}")
    return total_qty.quantize(Decimal("0.000001"))


def fifo_weight_for_qty(product, quantity, unit_weight=None) -> Decimal | None:
    """Total weight when taking `quantity` sealed bags FIFO."""
    qty_needed = Decimal(str(quantity or 0))
    if qty_needed <= 0:
        return Decimal("0")
    uw, _wu = next_fifo_packaging(product)
    total_w = Decimal("0")
    has_weight = bool(uw and uw > 0)
    for layer in _ordered_stock_layers(product):
        if qty_needed <= 0:
            break
        sealed = Decimal(str(layer.quantity_remaining or 0))
        if sealed <= 0:
            continue
        take = min(sealed, qty_needed)
        if has_weight:
            total_w += take * uw
        qty_needed -= take
    if qty_needed > 0:
        raise ValueError(f"Insufficient stock for {product.name}")
    return total_w if has_weight else None


def fifo_deduct_open_weight(
    product,
    sale_weight,
    movement_type,
    reference_type,
    reference_id,
    user_id,
    notes=None,
    entry_at=None,
    unit_weight=None,
):
    """
    Deduct an open (by-weight) sale for a single product weight.

    Example (50 kg bags, start 50 sealed):
      sell 25 → 49 sealed + 25 kg open
      sell 35 → uses 25 open + opens 1 bag → 48 sealed + 40 kg open
    """
    need = Decimal(str(sale_weight or 0))
    if need <= 0:
        raise ValueError("Sale weight must be greater than zero.")
    when = as_working_datetime(entry_at) if entry_at is not None else get_working_datetime()
    uw, _wu = next_fifo_packaging(product)
    if not uw or uw <= 0:
        raise ValueError(f"Unit weight missing for {product.name}.")
    bag_equiv = Decimal("0")
    total_cost = Decimal("0")
    layers = _ordered_stock_layers(product)

    for layer in layers:
        if need <= 0:
            break
        open_w = Decimal(str(layer.open_weight_remaining or 0))
        if open_w <= 0:
            continue
        unit_cost = Decimal(str(layer.unit_cost or 0))
        take = min(need, open_w)
        layer.open_weight_remaining = open_w - take
        need -= take
        qty_part = take / uw
        bag_equiv += qty_part
        total_cost += qty_part * unit_cost

    for layer in layers:
        if need <= 0:
            break
        unit_cost = Decimal(str(layer.unit_cost or 0))
        while need > 0 and Decimal(str(layer.quantity_remaining or 0)) >= 1:
            layer.quantity_remaining = Decimal(str(layer.quantity_remaining)) - Decimal("1")
            take = min(need, uw)
            leftover = uw - take
            layer.open_weight_remaining = Decimal(str(layer.open_weight_remaining or 0)) + leftover
            need -= take
            qty_part = take / uw
            bag_equiv += qty_part
            total_cost += qty_part * unit_cost

        sealed = Decimal(str(layer.quantity_remaining or 0))
        if need > 0 and sealed > 0:
            avail = sealed * uw
            take = min(need, avail)
            layer.quantity_remaining = sealed - (take / uw)
            need -= take
            qty_part = take / uw
            bag_equiv += qty_part
            total_cost += qty_part * unit_cost

    if need > Decimal("0.000001"):
        raise ValueError(f"Insufficient stock weight for {product.name}")

    # Prefer exact sold weight ÷ bag weight so DB/display don't become 9.99 from 0.333
    sold_w = Decimal(str(sale_weight))
    bag_equiv = (sold_w / uw).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    if bag_equiv <= 0 and sold_w > 0:
        bag_equiv = sold_w / uw

    sync_product_stock(product)
    movement = StockMovement(
        product_id=product.id,
        movement_type=movement_type,
        quantity=-bag_equiv,
        unit_cost=(total_cost / bag_equiv) if bag_equiv else Decimal("0"),
        balance_after=product.current_stock,
        reference_type=reference_type,
        reference_id=reference_id,
        notes=notes or f"Open {sold_w.normalize()} {product.weight_unit or 'kg'}",
        created_by_id=user_id,
        created_at=when,
    )
    db.session.add(movement)
    return total_cost, bag_equiv


def add_stock_layer(
    product_id,
    quantity,
    unit_cost,
    source_type,
    source_id=None,
    sale_price=None,
    notes=None,
    batch_number=None,
    expiry_date=None,
    vendor_id=None,
    invoice_no=None,
    received_at=None,
    unit_weight=None,
    weight_unit=None,
):
    uw = None
    if unit_weight is not None and str(unit_weight).strip() != "":
        try:
            parsed = Decimal(str(unit_weight))
            if parsed > 0:
                uw = parsed
        except Exception:
            uw = None
    wu = (str(weight_unit).strip() or None) if uw and weight_unit else None
    if uw and not wu:
        wu = "kg"
    layer = StockLayer(
        product_id=product_id,
        quantity_received=Decimal(str(quantity)),
        quantity_remaining=Decimal(str(quantity)),
        unit_cost=Decimal(str(unit_cost)),
        sale_price=Decimal(str(sale_price)) if sale_price is not None else None,
        unit_weight=uw,
        weight_unit=wu,
        open_weight_remaining=Decimal("0"),
        source_type=source_type,
        source_id=source_id,
        batch_number=(str(batch_number).strip() or None) if batch_number else None,
        expiry_date=expiry_date,
        vendor_id=int(vendor_id) if vendor_id else None,
        invoice_no=(str(invoice_no).strip() or None) if invoice_no else None,
        received_at=as_working_datetime(received_at) if received_at is not None else get_working_datetime(),
        notes=notes,
    )
    db.session.add(layer)
    return layer

def fifo_deduct(
    product,
    quantity,
    movement_type,
    reference_type,
    reference_id,
    user_id,
    notes=None,
    entry_at=None,
    unit_weight=None,
):
    """Deduct sealed bag/unit quantity (full-bag sales) FIFO."""
    qty_needed = Decimal(str(quantity))
    total_cost = Decimal("0")
    when = as_working_datetime(entry_at) if entry_at is not None else get_working_datetime()
    for layer in _ordered_stock_layers(product):
        if qty_needed <= 0:
            break
        sealed = Decimal(str(layer.quantity_remaining or 0))
        if sealed <= 0:
            continue
        take = min(sealed, qty_needed)
        layer.quantity_remaining = sealed - take
        total_cost += take * Decimal(str(layer.unit_cost or 0))
        qty_needed -= take
    if qty_needed > 0:
        raise ValueError(f"Insufficient stock for {product.name}")
    sync_product_stock(product)
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
        created_at=when,
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
    batch_number=None,
    expiry_date=None,
    vendor_id=None,
    invoice_no=None,
    entry_at=None,
    unit_weight=None,
    weight_unit=None,
):
    qty = Decimal(str(quantity))
    when = as_working_datetime(entry_at) if entry_at is not None else get_working_datetime()
    product.purchase_price = Decimal(str(unit_cost))
    if sale_price is not None:
        product.sale_price = Decimal(str(sale_price))
    elif product.sale_price is None:
        product.sale_price = Decimal("0")

    # One product = one weight: update product, sync all batches, store same on layer
    layer_uw = unit_weight
    layer_wu = weight_unit
    if layer_uw is not None and str(layer_uw).strip() != "":
        try:
            parsed = Decimal(str(layer_uw))
            if parsed > 0:
                product.unit_weight = parsed
                product.weight_unit = (str(layer_wu).strip() if layer_wu else None) or "kg"
                _sync_all_layer_weights(product)
        except Exception:
            pass
    layer_uw = product.unit_weight
    layer_wu = product.weight_unit

    layer_sale = sale_price if sale_price is not None else product.sale_price
    add_stock_layer(
        product.id,
        qty,
        unit_cost,
        source_type,
        source_id,
        sale_price=layer_sale,
        notes=notes,
        batch_number=batch_number,
        expiry_date=expiry_date,
        vendor_id=vendor_id,
        invoice_no=invoice_no,
        received_at=when,
        unit_weight=layer_uw,
        weight_unit=layer_wu,
    )
    sync_product_stock(product)
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
        created_at=when,
    )
    db.session.add(movement)


def _sync_all_layer_weights(product):
    """Force every batch to the product's single unit weight."""
    uw = product.unit_weight
    wu = product.weight_unit or "kg"
    if uw is None or Decimal(str(uw or 0)) <= 0:
        return
    for layer in StockLayer.query.filter_by(product_id=product.id).all():
        layer.unit_weight = Decimal(str(uw))
        layer.weight_unit = wu


def next_fifo_sale_price(product, unit_weight=None):
    """Sale price from the oldest remaining batch (FIFO), else product default."""
    for layer in _ordered_stock_layers(product):
        if not _layer_has_stock(layer):
            continue
        if layer.sale_price is not None:
            return Decimal(str(layer.sale_price))
        return Decimal(str(product.sale_price or 0))
    return Decimal(str(product.sale_price or 0))


def adjust_batch(layer, new_qty, user_id, notes=None, entry_at=None):
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

    when = as_working_datetime(entry_at) if entry_at is not None else get_working_datetime()
    layer.quantity_remaining = new_qty
    # Keep purchased qty as original; if stock is increased above it, raise purchased too
    received = Decimal(str(layer.quantity_received if layer.quantity_received is not None else old_qty))
    if new_qty > received:
        layer.quantity_received = new_qty
    elif layer.quantity_received is None:
        layer.quantity_received = received
    sync_product_stock(product)

    adj_type = "increase" if delta > 0 else "decrease"
    db.session.add(
        InventoryAdjustment(
            product_id=product.id,
            adjustment_type=adj_type,
            quantity=abs(delta),
            notes=notes or f"Batch #{layer.id} adjust {old_qty} → {new_qty}",
            created_by_id=user_id,
            created_at=when,
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
            created_at=when,
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
            created_at=get_working_datetime(),
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
    """Sum open layer cost (sealed bags + open weight), dashboard-safe."""
    layers = (
        StockLayer.query.filter(
            db.or_(
                StockLayer.quantity_remaining > 0,
                StockLayer.open_weight_remaining > 0,
            )
        ).all()
    )
    total = Decimal("0")
    for layer in layers:
        total += layer.stock_qty_equivalent * Decimal(str(layer.unit_cost or 0))
    return total

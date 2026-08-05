from decimal import Decimal

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
    """Return (unit_weight, weight_unit) for a batch, falling back to product."""
    product = product or getattr(layer, "product", None)
    uw = None
    if layer.unit_weight is not None and Decimal(str(layer.unit_weight)) > 0:
        uw = Decimal(str(layer.unit_weight))
    elif product is not None and product.unit_weight is not None and Decimal(str(product.unit_weight or 0)) > 0:
        uw = Decimal(str(product.unit_weight))
    wu = (layer.weight_unit or (product.weight_unit if product else None) or "kg") if uw else None
    return uw, wu


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


def _layer_matches_packaging(layer, product, unit_weight) -> bool:
    """True when layer belongs to the requested packaging (or no filter given)."""
    want = normalize_unit_weight(unit_weight)
    if want is None:
        return True
    got, _wu = layer_packaging(layer, product)
    got_n = normalize_unit_weight(got)
    return got_n is not None and got_n == want


def _ordered_stock_layers(product):
    return (
        StockLayer.query.filter_by(product_id=product.id)
        .order_by(StockLayer.received_at.asc(), StockLayer.id.asc())
        .all()
    )


def list_packaging_options(product):
    """
    In-stock packaging groups for a product (FIFO within each weight).

    Each option: unit_weight, weight_unit, sale_price (FIFO for that weight),
    stock_qty (bag-equivalent), stock_display.
    """
    from app.utils.weight_utils import format_qty_display

    options = []
    index = {}
    for layer in _ordered_stock_layers(product):
        if not _layer_has_stock(layer):
            continue
        uw, wu = layer_packaging(layer, product)
        key = str(normalize_unit_weight(uw)) if uw is not None else "__none__"
        if key not in index:
            price = (
                Decimal(str(layer.sale_price))
                if layer.sale_price is not None
                else Decimal(str(product.sale_price or 0))
            )
            opt = {
                "unit_weight": float(uw) if uw is not None else None,
                "weight_unit": wu or product.weight_unit or "kg",
                "sale_price": float(price),
                "stock_qty": Decimal("0"),
            }
            index[key] = opt
            options.append(opt)
        index[key]["stock_qty"] += layer.stock_qty_equivalent

    for opt in options:
        qty = opt["stock_qty"]
        opt["stock_qty"] = float(qty)
        if opt["unit_weight"]:
            opt["stock_display"] = format_qty_display(
                qty, Decimal(str(opt["unit_weight"])), opt["weight_unit"]
            )
        else:
            opt["stock_display"] = format_qty_display(qty, None, None)
        opt["label"] = (
            f"{opt['unit_weight']:g} {opt['weight_unit']} · {opt['sale_price']:.2f} · {opt['stock_display']}"
            if opt["unit_weight"]
            else f"Units · {opt['sale_price']:.2f} · {opt['stock_display']}"
        )
    return options


def next_fifo_packaging(product, unit_weight=None):
    """Packaging of the oldest stocked batch (optionally within one weight)."""
    want = normalize_unit_weight(unit_weight)
    for layer in _ordered_stock_layers(product):
        if not _layer_has_stock(layer):
            continue
        if not _layer_matches_packaging(layer, product, want):
            continue
        return layer_packaging(layer, product)
    if want is not None:
        return want, product.weight_unit or "kg"
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


def weight_to_stock_qty_fifo(product, sale_weight, unit_weight=None) -> Decimal:
    """
    Bag-equivalent qty for an open-weight sale (does not mutate stock).
    Uses open leftover first, then sealed bags — FIFO within the same packaging weight.
    """
    remaining_w = Decimal(str(sale_weight or 0))
    if remaining_w <= 0:
        raise ValueError("Sale weight must be greater than zero.")
    want = normalize_unit_weight(unit_weight)
    total_qty = Decimal("0")
    for layer in _ordered_stock_layers(product):
        if remaining_w <= 0:
            break
        if not _layer_has_stock(layer):
            continue
        if not _layer_matches_packaging(layer, product, want):
            continue
        uw, _wu = layer_packaging(layer, product)
        if not uw or uw <= 0:
            raise ValueError(f"Unit weight missing for a stock batch of {product.name}.")
        avail_w = _layer_available_weight(layer, product)
        if avail_w <= 0:
            continue
        take_w = min(remaining_w, avail_w)
        total_qty += take_w / uw
        remaining_w -= take_w
    if remaining_w > Decimal("0.000001"):
        label = f"{want:g} {product.weight_unit or 'kg'} " if want else ""
        raise ValueError(f"Insufficient {label}stock weight for {product.name}")
    return total_qty.quantize(Decimal("0.000001"))


def fifo_weight_for_qty(product, quantity, unit_weight=None) -> Decimal | None:
    """Total packaging weight when taking `quantity` sealed bags FIFO (same weight only)."""
    qty_needed = Decimal(str(quantity or 0))
    if qty_needed <= 0:
        return Decimal("0")
    want = normalize_unit_weight(unit_weight)
    total_w = Decimal("0")
    has_weight = False
    for layer in _ordered_stock_layers(product):
        if qty_needed <= 0:
            break
        sealed = Decimal(str(layer.quantity_remaining or 0))
        if sealed <= 0:
            continue
        if not _layer_matches_packaging(layer, product, want):
            continue
        take = min(sealed, qty_needed)
        uw, _wu = layer_packaging(layer, product)
        if uw and uw > 0:
            has_weight = True
            total_w += take * uw
        qty_needed -= take
    if qty_needed > 0:
        label = f"{want:g} {product.weight_unit or 'kg'} " if want else ""
        raise ValueError(f"Insufficient {label}stock for {product.name}")
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
    Deduct an open (by-weight) sale.

    Uses open leftover kg first, then opens whole sealed bags and keeps the
    unused part of each opened bag as open_weight_remaining.

    FIFO only within the same packaging weight when unit_weight is set.

    Example (50 kg bags, start 53 sealed):
      sell 25 → 52 sealed + 25 kg open
      sell 25 → 52 sealed + 0 kg open   (uses leftover)
      sell 25 → 51 sealed + 25 kg open

    Display is always sealed bags + open kg (never 51.5 → \"51 + 25 kg\").
    """
    need = Decimal(str(sale_weight or 0))
    if need <= 0:
        raise ValueError("Sale weight must be greater than zero.")
    when = as_working_datetime(entry_at) if entry_at is not None else get_working_datetime()
    want = normalize_unit_weight(unit_weight)
    bag_equiv = Decimal("0")
    total_cost = Decimal("0")

    for layer in _ordered_stock_layers(product):
        if need <= 0:
            break
        if not _layer_has_stock(layer):
            continue
        if not _layer_matches_packaging(layer, product, want):
            continue
        uw, _wu = layer_packaging(layer, product)
        if not uw or uw <= 0:
            raise ValueError(f"Unit weight missing for a stock batch of {product.name}.")
        unit_cost = Decimal(str(layer.unit_cost or 0))

        # 1) Consume existing open leftover first
        open_w = Decimal(str(layer.open_weight_remaining or 0))
        if open_w > 0:
            take = min(need, open_w)
            layer.open_weight_remaining = open_w - take
            need -= take
            qty_part = take / uw
            bag_equiv += qty_part
            total_cost += qty_part * unit_cost

        # 2) Open whole sealed bags as needed
        while need > 0 and Decimal(str(layer.quantity_remaining or 0)) >= 1:
            layer.quantity_remaining = Decimal(str(layer.quantity_remaining)) - Decimal("1")
            take = min(need, uw)
            leftover = uw - take
            layer.open_weight_remaining = Decimal(str(layer.open_weight_remaining or 0)) + leftover
            need -= take
            qty_part = take / uw
            bag_equiv += qty_part
            total_cost += qty_part * unit_cost

        # 3) Legacy fractional sealed qty (pre-migration leftovers)
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
        label = f"{want:g} {product.weight_unit or 'kg'} " if want else ""
        raise ValueError(f"Insufficient {label}stock weight for {product.name}")

    sync_product_stock(product)
    movement = StockMovement(
        product_id=product.id,
        movement_type=movement_type,
        quantity=-bag_equiv,
        unit_cost=(total_cost / bag_equiv) if bag_equiv else Decimal("0"),
        balance_after=product.current_stock,
        reference_type=reference_type,
        reference_id=reference_id,
        notes=notes,
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
    """Deduct sealed bag/unit quantity (full-bag sales). FIFO within same packaging weight."""
    qty_needed = Decimal(str(quantity))
    total_cost = Decimal("0")
    when = as_working_datetime(entry_at) if entry_at is not None else get_working_datetime()
    want = normalize_unit_weight(unit_weight)
    for layer in _ordered_stock_layers(product):
        if qty_needed <= 0:
            break
        sealed = Decimal(str(layer.quantity_remaining or 0))
        if sealed <= 0:
            continue
        if not _layer_matches_packaging(layer, product, want):
            continue
        take = min(sealed, qty_needed)
        layer.quantity_remaining = sealed - take
        total_cost += take * Decimal(str(layer.unit_cost or 0))
        qty_needed -= take
    if qty_needed > 0:
        label = f"{want:g} {product.weight_unit or 'kg'} " if want else ""
        raise ValueError(f"Insufficient {label}stock for {product.name}")
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
    # Keep product list prices as latest defaults for display
    product.purchase_price = Decimal(str(unit_cost))
    if sale_price is not None:
        product.sale_price = Decimal(str(sale_price))
    elif product.sale_price is None:
        product.sale_price = Decimal("0")

    # Batch keeps its own packaging; product stores latest default for new UI only.
    layer_uw = unit_weight
    layer_wu = weight_unit
    if layer_uw is None or str(layer_uw).strip() == "":
        layer_uw = product.unit_weight
        layer_wu = product.weight_unit
    else:
        # Update product default to this receipt's packaging (does not rewrite old batches).
        try:
            parsed = Decimal(str(layer_uw))
            if parsed > 0:
                product.unit_weight = parsed
                product.weight_unit = (str(layer_wu).strip() if layer_wu else None) or "kg"
        except Exception:
            pass

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


def next_fifo_sale_price(product, unit_weight=None):
    """Sale price from the oldest remaining batch of this packaging (FIFO), else product default."""
    want = normalize_unit_weight(unit_weight)
    for layer in _ordered_stock_layers(product):
        if not _layer_has_stock(layer):
            continue
        if not _layer_matches_packaging(layer, product, want):
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

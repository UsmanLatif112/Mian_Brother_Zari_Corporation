from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func

from app.extensions import db
from app.models import InventoryAdjustment, StockLayer, StockMovement
from app.utils.working_date import as_working_datetime, get_working_datetime


def _money(val) -> str:
    return f"{Decimal(str(val or 0)):.2f}"


def _qty_items_label(qty) -> str:
    q = Decimal(str(qty or 0))
    aq = abs(q)
    if aq == aq.to_integral_value():
        n = int(aq)
        return f"{n} item" if n == 1 else f"{n} items"
    return f"{aq.normalize()} items"


def _reprice_movement_notes(changes, extra_notes=None) -> str:
    """Always label as Reprice with the actual change; ignore generic edit labels."""
    base = "Reprice: " + ", ".join(changes)
    extra = (extra_notes or "").strip()
    if not extra:
        return base
    generic = {
        "edited inventory entry",
        "purchase price change",
        "purchase price changed",
        "price change",
    }
    if extra.lower() in generic:
        return base
    return f"{base}; {extra}"


def _apply_cost_note(existing, kind, qty, old_cost, new_cost) -> str:
    """Build movement notes for sale / return after a cost true-up."""
    change = f"cost {_money(old_cost)}→{_money(new_cost)}"
    if kind == "sale":
        core = f"Sale {_qty_items_label(qty)}; {change}"
    elif kind == "return":
        core = f"Return {_qty_items_label(qty)}; {change}"
    else:
        core = change
    prev = (existing or "").strip()
    if not prev:
        return core
    # Keep invoice/return refs; replace prior cost-adjust fragments
    if "cost " in prev and "→" in prev:
        return core
    if prev.lower().startswith("sale ") or prev.lower().startswith("return "):
        return core
    return f"{prev}; {change}"


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


def _ordered_stock_layers(product, prefer_layer_ids=None):
    layers = (
        StockLayer.query.filter_by(product_id=product.id)
        .order_by(StockLayer.received_at.asc(), StockLayer.id.asc())
        .all()
    )
    if not prefer_layer_ids:
        return layers
    prefer = [int(x) for x in prefer_layer_ids]
    by_id = {layer.id: layer for layer in layers}
    head = [by_id[i] for i in prefer if i in by_id]
    skip = set(prefer)
    tail = [layer for layer in layers if layer.id not in skip]
    return head + tail


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
    """Rebuild product.current_stock from sealed bags + open weight − open backorders."""
    layers = StockLayer.query.filter_by(product_id=product.id).all()
    total = Decimal("0")
    for layer in layers:
        total += layer.stock_qty_equivalent
    try:
        from app.services.inventory_loss_service import open_backorder_qty

        total -= open_backorder_qty(product.id)
    except Exception:
        pass
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
        # Allow qty math for negative-stock sales (caller must pass allow_negative to deduct).
        total_qty += remaining_w / uw
    return total_qty.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def fifo_weight_for_qty(product, quantity, unit_weight=None) -> Decimal | None:
    """Total weight when taking `quantity` sealed bags FIFO."""
    qty_needed = Decimal(str(quantity or 0))
    if qty_needed <= 0:
        return Decimal("0")
    uw, _wu = next_fifo_packaging(product)
    total_w = Decimal("0")
    has_weight = bool(uw and uw > 0)
    remaining = qty_needed
    for layer in _ordered_stock_layers(product):
        if remaining <= 0:
            break
        sealed = Decimal(str(layer.quantity_remaining or 0))
        if sealed <= 0:
            continue
        take = min(sealed, remaining)
        if has_weight:
            total_w += take * uw
        remaining -= take
    if remaining > 0:
        # Negative-stock sales: weight = product packaging × shortfall bags
        if has_weight:
            total_w += remaining * uw
        else:
            return None
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
    allow_negative=False,
    prefer_layer_ids=None,
):
    """
    Deduct an open (by-weight) sale for a single product weight.

    Returns (total_cost, bag_equiv, backorder_qty, backorder_weight).
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
    layers = _ordered_stock_layers(product, prefer_layer_ids)
    backorder_qty = Decimal("0")
    backorder_weight = Decimal("0")

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
        if not allow_negative:
            raise ValueError(f"Insufficient stock weight for {product.name}")
        backorder_weight = need
        backorder_qty = (need / uw).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        need = Decimal("0")

    sold_w = Decimal(str(sale_weight))
    bag_equiv = (sold_w / uw).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    if bag_equiv <= 0 and sold_w > 0:
        bag_equiv = sold_w / uw

    sync_product_stock(product)
    # Backorder row not yet inserted — temporarily reflect debt on product stock
    if backorder_qty > 0:
        product.current_stock = Decimal(str(product.current_stock or 0)) - backorder_qty

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
    return total_cost, bag_equiv, backorder_qty, backorder_weight


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
    sealed_qty=None,
    open_weight=None,
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
    total = Decimal(str(quantity))
    if sealed_qty is not None:
        sealed = Decimal(str(sealed_qty))
        open_w = Decimal(str(open_weight or 0))
    else:
        sealed = total
        open_w = Decimal("0")
    layer = StockLayer(
        product_id=product_id,
        quantity_received=total,
        quantity_remaining=sealed,
        unit_cost=Decimal(str(unit_cost)),
        sale_price=Decimal(str(sale_price)) if sale_price is not None else None,
        unit_weight=uw,
        weight_unit=wu,
        open_weight_remaining=open_w,
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
    db.session.flush()
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
    allow_negative=False,
    prefer_layer_ids=None,
):
    """Deduct sealed bag/unit quantity. Returns (total_cost, backorder_qty)."""
    qty_needed = Decimal(str(quantity))
    requested = qty_needed
    total_cost = Decimal("0")
    when = as_working_datetime(entry_at) if entry_at is not None else get_working_datetime()
    for layer in _ordered_stock_layers(product, prefer_layer_ids):
        if qty_needed <= 0:
            break
        sealed = Decimal(str(layer.quantity_remaining or 0))
        if sealed <= 0:
            continue
        take = min(sealed, qty_needed)
        layer.quantity_remaining = sealed - take
        total_cost += take * Decimal(str(layer.unit_cost or 0))
        qty_needed -= take
    backorder_qty = Decimal("0")
    if qty_needed > 0:
        if not allow_negative:
            raise ValueError(f"Insufficient stock for {product.name}")
        backorder_qty = qty_needed
    sync_product_stock(product)
    if backorder_qty > 0:
        product.current_stock = Decimal(str(product.current_stock or 0)) - backorder_qty
    movement = StockMovement(
        product_id=product.id,
        movement_type=movement_type,
        quantity=-requested,
        unit_cost=total_cost / requested if requested else Decimal("0"),
        balance_after=product.current_stock,
        reference_type=reference_type,
        reference_id=reference_id,
        notes=notes
        or (
            f"Sale {_qty_items_label(requested)}"
            if movement_type == "sale_out"
            else None
        ),
        created_by_id=user_id,
        created_at=when,
    )
    db.session.add(movement)
    return total_cost, backorder_qty


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
    sealed_qty=None,
    open_weight=None,
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
    layer = add_stock_layer(
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
        sealed_qty=sealed_qty,
        open_weight=open_weight,
    )
    try:
        from app.services.inventory_loss_service import fulfill_backorders_for_product

        fulfill_backorders_for_product(product, qty, unit_cost, user_id=user_id)
    except Exception:
        pass
    sync_product_stock(product)
    if source_type == "sale_return":
        mv_type = "sale_return_in"
    elif source_type in ("sale_void", "sale_return_void", "sale_edit"):
        mv_type = "sale_void_in"
    else:
        mv_type = "purchase_in"

    mv_notes = notes
    if source_type == "purchase":
        from app.utils.movement_labels import format_purchase_movement_notes

        if sealed_qty is not None:
            mv_notes = format_purchase_movement_notes(
                sealed_qty, open_weight, layer_uw, layer_wu, notes
            )
        elif notes and not str(notes).lower().startswith("purchase"):
            mv_notes = f"Purchase: {notes}" if notes else "Purchase in"

    movement = StockMovement(
        product_id=product.id,
        movement_type=mv_type,
        quantity=qty,
        unit_cost=Decimal(str(unit_cost)),
        balance_after=product.current_stock,
        reference_type=source_type,
        reference_id=source_id,
        notes=mv_notes,
        created_by_id=user_id,
        created_at=when,
    )
    db.session.add(movement)
    return layer


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
    uw = layer.effective_unit_weight
    wu = layer.effective_weight_unit or "kg"
    if uw and uw > 0:
        qty_note = f"sealed {old_qty.normalize()}→{new_qty.normalize()} bags"
    else:
        qty_note = f"qty {old_qty.normalize()}→{new_qty.normalize()}"
    default_notes = f"Qty adjust: {qty_note}"
    db.session.add(
        InventoryAdjustment(
            product_id=product.id,
            adjustment_type=adj_type,
            quantity=abs(delta),
            notes=notes or default_notes,
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
            notes=notes or default_notes,
            created_by_id=user_id,
            created_at=when,
        )
    )
    # Shortage (e.g. bought 50, count 49) → non-cash inventory loss (does not hit cash)
    if delta < 0:
        try:
            from app.services.inventory_loss_service import record_inventory_loss
            from app.utils.working_date import get_working_date

            loss_date = when.date() if hasattr(when, "date") else get_working_date()
            record_inventory_loss(
                product_id=product.id,
                quantity=abs(delta),
                unit_cost=layer.unit_cost or 0,
                source_type="adjustment_shortage",
                source_id=layer.id,
                loss_date=loss_date,
                notes=notes or f"Batch #{layer.id} shortage {old_qty} → {new_qty}",
                user_id=user_id,
            )
        except Exception:
            pass
    return layer


def reprice_batch(layer, unit_cost=None, sale_price=None, user_id=None, notes=None):
    """
    Update purchase/sale price on this batch.

    Remaining stock uses the new cost immediately. Sale lines / returns / losses
    drawn from this batch are forced to the new unit cost so dashboard Total Cost
    and Gross Profit match the corrected purchase price.
    """
    layer = db.session.get(StockLayer, layer.id if hasattr(layer, "id") else int(layer))
    if not layer:
        raise ValueError("Batch not found.")

    changes = []
    old_unit_cost = Decimal(str(layer.unit_cost or 0))
    cost_changed = False
    if unit_cost is not None:
        new_cost = Decimal(str(unit_cost))
        if new_cost != old_unit_cost:
            layer.unit_cost = new_cost
            changes.append(f"cost {_money(old_unit_cost)}→{_money(layer.unit_cost)}")
            cost_changed = True
    if sale_price is not None:
        old_sale = layer.sale_price
        layer.sale_price = Decimal(str(sale_price))
        if Decimal(str(old_sale or 0)) != Decimal(str(layer.sale_price or 0)):
            changes.append(f"sale {_money(old_sale)}→{_money(layer.sale_price)}")

    if changes:
        bal = Decimal("0")
        if layer.product is not None:
            bal = Decimal(str(layer.product.current_stock or 0))
        db.session.add(
            StockMovement(
                product_id=layer.product_id,
                movement_type="reprice",
                quantity=Decimal("0"),
                unit_cost=layer.unit_cost,
                balance_after=bal,
                reference_type="stock_layer",
                reference_id=layer.id,
                notes=_reprice_movement_notes(changes, notes),
                created_by_id=user_id,
                created_at=get_working_datetime(),
            )
        )

    if unit_cost is not None:
        # Always sync sold COGS to this batch's cost (handles missed prior reprices).
        prefer_old = old_unit_cost if cost_changed else None
        apply_layer_unit_cost_to_sold(
            layer,
            Decimal(str(layer.unit_cost or 0)),
            prefer_old_unit_cost=prefer_old,
        )
        if cost_changed and (layer.source_type or "") == "purchase" and layer.source_id:
            try:
                from app.services.purchase_service import amend_purchase_for_layer

                purchased = Decimal(
                    str(
                        layer.quantity_received
                        if layer.quantity_received is not None
                        else layer.quantity_remaining
                        or 0
                    )
                )
                amend_purchase_for_layer(
                    layer,
                    old_purchased=purchased,
                    new_purchased=purchased,
                    old_unit_cost=old_unit_cost,
                    new_unit_cost=Decimal(str(layer.unit_cost or 0)),
                )
            except Exception:
                pass
    return layer


def _product_layer_count(product_id) -> int:
    return int(
        db.session.query(func.count(StockLayer.id))
        .filter(StockLayer.product_id == int(product_id))
        .scalar()
        or 0
    )


def apply_layer_unit_cost_to_sold(
    layer, new_unit_cost, prefer_old_unit_cost=None
) -> dict:
    """
    Force COGS for qty sold from this batch to new_unit_cost.

    Prefer rewriting lines still at prefer_old_unit_cost (when the purchase
    price just changed). If this product has a single batch — or leftover budget
    remains after that pass — rewrite remaining sold lines that are not already
    at the new cost. That recovers cases where an earlier reprice updated the
    layer but left SaleItem.cost_of_goods on the typo price.
    """
    from app.models import InventoryLoss, Sale, SaleItem, SaleReturnItem

    new = Decimal(str(new_unit_cost or 0))
    prefer_old = (
        Decimal(str(prefer_old_unit_cost))
        if prefer_old_unit_cost is not None
        else None
    )
    result = {"sale_items": 0, "returns": 0, "losses": 0, "movements": 0}
    tol = Decimal("0.02")
    try:
        budget = Decimal(str(getattr(layer, "quantity_used", None) or 0))
    except Exception:
        budget = Decimal("0")

    single_batch = _product_layer_count(layer.product_id) == 1

    # Losses tied to this layer always follow the new unit cost.
    for loss in InventoryLoss.query.filter(
        InventoryLoss.product_id == layer.product_id,
        InventoryLoss.source_id == layer.id,
    ).all():
        lcost = Decimal(str(loss.unit_cost or 0))
        if abs(lcost - new) <= tol:
            continue
        qty = Decimal(str(loss.quantity or 0))
        loss.unit_cost = new
        loss.amount = (qty * new).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        result["losses"] += 1

    if budget <= 0:
        return result

    received_at = getattr(layer, "received_at", None)
    items_q = (
        SaleItem.query.join(Sale, Sale.id == SaleItem.sale_id)
        .filter(SaleItem.product_id == layer.product_id)
        .order_by(Sale.sale_date.asc(), Sale.id.asc(), SaleItem.id.asc())
    )
    if received_at is not None:
        try:
            items_q = items_q.filter(Sale.sale_date >= received_at.date())
        except Exception:
            pass
    items = items_q.all()

    def _rewrite_pass(match_mode: str):
        nonlocal budget
        updated_ids: list[int] = []
        old_pers: dict[int, Decimal] = {}
        for item in items:
            if budget <= 0:
                break
            qty = Decimal(str(item.quantity or 0))
            if qty <= 0:
                continue
            cogs = Decimal(str(item.cost_of_goods or 0))
            per = (cogs / qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if abs(per - new) <= tol:
                # Already correct — still consume budget for FIFO allocation
                take = min(qty, budget)
                budget -= take
                continue
            if match_mode == "prefer_old":
                if prefer_old is None:
                    continue
                if abs(per - prefer_old) > tol and abs(cogs - (qty * prefer_old)) > Decimal(
                    "0.05"
                ):
                    continue
            elif match_mode == "force_mismatch":
                if not single_batch:
                    # Multi-batch: only force if this looks like the old typo cost
                    # already handled in prefer_old; skip ambiguous rows.
                    continue
            else:
                continue

            take = min(qty, budget)
            if take <= 0:
                continue
            # Replace take units at current per with new unit cost
            item.cost_of_goods = (cogs - (take * per) + (take * new)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            updated_ids.append(int(item.id))
            old_pers[int(item.id)] = per
            result["sale_items"] += 1
            budget -= take
        return updated_ids, old_pers

    # Pass 1: lines still carrying the previous batch cost
    updated_item_ids, old_pers = _rewrite_pass("prefer_old")
    # Pass 2: single-batch recovery — any sold line not yet at new cost
    more_ids, more_pers = _rewrite_pass("force_mismatch")
    updated_item_ids.extend(more_ids)
    old_pers.update(more_pers)

    if updated_item_ids:
        for ri in SaleReturnItem.query.filter(
            SaleReturnItem.sale_item_id.in_(updated_item_ids)
        ).all():
            si = db.session.get(SaleItem, ri.sale_item_id)
            if not si:
                continue
            sqty = Decimal(str(si.quantity or 0))
            rqty = Decimal(str(ri.quantity or 0))
            if sqty > 0 and rqty > 0:
                ri.cost_of_goods = (
                    Decimal(str(si.cost_of_goods or 0)) * (rqty / sqty)
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            result["returns"] += 1

        result["movements"] += _sync_sale_return_movements_after_cost_change(
            layer.product_id, updated_item_ids, old_pers, new
        )
    elif single_batch:
        # Sale items may already be correct; still fix stale movement unit costs
        moves = StockMovement.query.filter(
            StockMovement.product_id == layer.product_id,
            StockMovement.movement_type == "sale_out",
        ).all()
        leftover = Decimal(str(getattr(layer, "quantity_used", None) or 0))
        for mv in moves:
            if leftover <= 0:
                break
            mv_cost = Decimal(str(mv.unit_cost or 0))
            qty = abs(Decimal(str(mv.quantity or 0)))
            if abs(mv_cost - new) <= tol:
                leftover -= qty
                continue
            old_c = mv_cost
            mv.unit_cost = new
            mv.notes = _apply_cost_note(mv.notes, "sale", mv.quantity, old_c, new)
            result["movements"] += 1
            leftover -= qty

    return result


def _sync_sale_return_movements_after_cost_change(
    product_id, updated_item_ids, old_unit_by_item, new_unit_cost
) -> int:
    """Update Sale Out / Sale Return In movement cost + notes after COGS true-up."""
    from app.models import SaleItem, SaleReturnItem

    if not updated_item_ids:
        return 0
    new = Decimal(str(new_unit_cost or 0))
    tol = Decimal("0.02")
    changed = 0

    sale_ids = {
        int(row.sale_id)
        for row in SaleItem.query.filter(SaleItem.id.in_(updated_item_ids)).all()
    }
    if sale_ids:
        moves = StockMovement.query.filter(
            StockMovement.product_id == product_id,
            StockMovement.movement_type == "sale_out",
            StockMovement.reference_type == "sale",
            StockMovement.reference_id.in_(sale_ids),
        ).all()
        for mv in moves:
            mv_cost = Decimal(str(mv.unit_cost or 0))
            old_guess = None
            for iid in updated_item_ids:
                si = db.session.get(SaleItem, iid)
                if si and int(si.sale_id) == int(mv.reference_id or 0):
                    old_guess = old_unit_by_item.get(iid, mv_cost)
                    break
            if old_guess is None:
                continue
            if abs(mv_cost - new) > tol:
                mv.unit_cost = new
            mv.notes = _apply_cost_note(mv.notes, "sale", mv.quantity, old_guess, new)
            changed += 1

    return_ids = {
        int(ri.sale_return_id)
        for ri in SaleReturnItem.query.filter(
            SaleReturnItem.sale_item_id.in_(updated_item_ids)
        ).all()
        if ri.sale_return_id
    }
    if return_ids:
        r_moves = StockMovement.query.filter(
            StockMovement.product_id == product_id,
            StockMovement.movement_type == "sale_return_in",
            StockMovement.reference_type == "sale_return",
            StockMovement.reference_id.in_(return_ids),
        ).all()
        for mv in r_moves:
            mv_cost = Decimal(str(mv.unit_cost or 0))
            old_guess = mv_cost
            for ri in SaleReturnItem.query.filter(
                SaleReturnItem.sale_return_id == mv.reference_id,
                SaleReturnItem.sale_item_id.in_(updated_item_ids),
            ).all():
                old_guess = old_unit_by_item.get(int(ri.sale_item_id), mv_cost)
                break
            if abs(mv_cost - new) > tol:
                mv.unit_cost = new
            keep = (mv.notes or "").strip()
            change = f"cost {_money(old_guess)}→{_money(new)}"
            if keep and "cost " not in keep:
                mv.notes = f"{keep}; {change}"
            else:
                mv.notes = _apply_cost_note(keep, "return", mv.quantity, old_guess, new)
            changed += 1

    return changed


def repair_sold_cogs_to_layer_cost(layer, target_cost) -> dict:
    """Recovery helper: force sold COGS to the batch cost (single-batch safe)."""
    return apply_layer_unit_cost_to_sold(
        layer, target_cost, prefer_old_unit_cost=None
    )


def true_up_cogs_after_layer_cost_change(layer, old_unit_cost, new_unit_cost) -> dict:
    """Rebuild sold COGS after a purchase-price correction on this batch."""
    return apply_layer_unit_cost_to_sold(
        layer, new_unit_cost, prefer_old_unit_cost=old_unit_cost
    )


def reprice_all_remaining(product, unit_cost=None, sale_price=None, user_id=None, notes=None):
    """Reprice open batches for a product; falls back to latest batch if none open."""
    layers = (
        StockLayer.query.filter_by(product_id=product.id)
        .filter(
            db.or_(
                StockLayer.quantity_remaining > 0,
                StockLayer.open_weight_remaining > 0,
            )
        )
        .all()
    )
    if not layers:
        layers = (
            StockLayer.query.filter_by(product_id=product.id)
            .order_by(StockLayer.id.desc())
            .limit(1)
            .all()
        )
        if not layers:
            raise ValueError("No stock batch found to reprice.")
    for layer in layers:
        reprice_batch(
            layer, unit_cost=unit_cost, sale_price=sale_price, user_id=user_id, notes=notes
        )
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


def _end_of_day(as_of_date):
    from datetime import datetime, time

    return datetime.combine(as_of_date, time.max)


def product_stock_as_of(product_id: int, as_of_date) -> Decimal:
    """Product qty at end of as_of_date from last stock movement on/before that day."""
    if as_of_date is None:
        from app.models import Product

        product = db.session.get(Product, product_id)
        return Decimal(str(product.current_stock or 0)) if product else Decimal("0")
    last = (
        StockMovement.query.filter(
            StockMovement.product_id == product_id,
            StockMovement.created_at <= _end_of_day(as_of_date),
        )
        .order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
        .first()
    )
    return Decimal(str(last.balance_after)) if last else Decimal("0")


def products_stock_as_of(as_of_date, product_ids=None) -> dict:
    """Map product_id -> qty as of as_of_date (last movement on/before that day)."""
    if as_of_date is None:
        from app.models import Product

        q = Product.query.filter_by(is_deleted=False)
        if product_ids is not None:
            if not product_ids:
                return {}
            q = q.filter(Product.id.in_(product_ids))
        return {p.id: Decimal(str(p.current_stock or 0)) for p in q.all()}

    end_dt = _end_of_day(as_of_date)
    q = StockMovement.query.filter(StockMovement.created_at <= end_dt)
    if product_ids is not None:
        if not product_ids:
            return {}
        q = q.filter(StockMovement.product_id.in_(product_ids))
    rows = q.order_by(
        StockMovement.product_id.asc(),
        StockMovement.created_at.desc(),
        StockMovement.id.desc(),
    ).all()
    out = {}
    for row in rows:
        if row.product_id in out:
            continue
        out[row.product_id] = Decimal(str(row.balance_after or 0))
    return out


def _avg_unit_cost_as_of(product_id: int, as_of_date) -> Decimal:
    """Average unit cost of layers received on/before as_of_date."""
    end_dt = _end_of_day(as_of_date)
    layers = (
        StockLayer.query.filter(
            StockLayer.product_id == product_id,
            StockLayer.received_at <= end_dt,
        )
        .order_by(StockLayer.received_at.desc(), StockLayer.id.desc())
        .all()
    )
    if not layers:
        return Decimal("0")
    total_qty = Decimal("0")
    total_cost = Decimal("0")
    for layer in layers:
        qty = Decimal(str(layer.quantity_received or layer.quantity_remaining or 0))
        if qty <= 0:
            continue
        cost = Decimal(str(layer.unit_cost or 0))
        total_qty += qty
        total_cost += qty * cost
    if total_qty > 0:
        return (total_cost / total_qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return Decimal(str(layers[0].unit_cost or 0))


def stock_valuation_as_of(as_of_date, product_ids=None) -> Decimal:
    """
    Stock value as of end of as_of_date.

    Qty from StockMovement.balance_after; unit cost from layers received
    on/before that date. When as_of_date is None, use live stock_valuation().
    """
    if as_of_date is None:
        if product_ids is None:
            return stock_valuation()
        layers = StockLayer.query.filter(
            StockLayer.product_id.in_(product_ids),
            db.or_(
                StockLayer.quantity_remaining > 0,
                StockLayer.open_weight_remaining > 0,
            ),
        ).all()
        total = Decimal("0")
        for layer in layers:
            total += layer.stock_qty_equivalent * Decimal(str(layer.unit_cost or 0))
        return total

    stock_map = products_stock_as_of(as_of_date, product_ids=product_ids)
    total = Decimal("0")
    for pid, qty in stock_map.items():
        if qty <= 0:
            continue
        total += qty * _avg_unit_cost_as_of(pid, as_of_date)
    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
"""Product unit weight helpers (partial-weight sales + stock display)."""

from __future__ import annotations

from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

WEIGHT_UNITS = (
    ("kg", "kg"),
    ("g", "g"),
    ("L", "L"),
    ("ml", "ml"),
)
WEIGHT_UNIT_SET = {u for u, _ in WEIGHT_UNITS}


def normalize_weight_unit(value: str | None) -> str | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    # Normalize common aliases
    aliases = {
        "KG": "kg",
        "Kg": "kg",
        "kilogram": "kg",
        "kilograms": "kg",
        "G": "g",
        "gram": "g",
        "grams": "g",
        "l": "L",
        "liter": "L",
        "litre": "L",
        "liters": "L",
        "litres": "L",
        "ML": "ml",
        "Ml": "ml",
        "milliliter": "ml",
        "millilitre": "ml",
    }
    unit = aliases.get(raw, raw)
    if unit not in WEIGHT_UNIT_SET:
        raise ValueError("Weight unit must be kg, g, L, or ml.")
    return unit


def parse_unit_weight(value) -> Decimal | None:
    if value in (None, ""):
        return None
    w = Decimal(str(value))
    if w < 0:
        raise ValueError("Unit weight cannot be negative.")
    if w == 0:
        return None
    return w


def product_has_weight(product) -> bool:
    try:
        return Decimal(str(getattr(product, "unit_weight", None) or 0)) > 0
    except Exception:
        return False


def clean_number(value) -> str:
    """Strip trailing zeros: 50.000 → 50, 12.500 → 12.5"""
    try:
        return _clean_number(Decimal(str(value or 0)))
    except Exception:
        return str(value or 0)


def _clean_number(value: Decimal) -> str:
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text or "0"


def split_qty_units_and_weight(
    qty, unit_weight
) -> tuple[Decimal, Decimal, Decimal]:
    """
    Split bag-equivalent qty into (sealed_bags, open_weight, sign).

    Matches inventory: sealed whole bags + leftover kg (no “borrow a bag”).
    Example (unit_weight=50 kg):
      49.5 bags → 49 sealed + 25 kg open
      0.2 bags  → 0 sealed + 10 kg open
      2 bags    → 2 sealed + 0 kg
    """
    q = Decimal(str(qty or 0))
    if q == 0:
        return Decimal("0"), Decimal("0"), Decimal("0")
    sign = Decimal("-1") if q < 0 else Decimal("1")
    abs_q = abs(q)
    uw = Decimal(str(unit_weight or 0))
    if uw <= 0:
        return abs_q, Decimal("0"), sign
    full = abs_q.to_integral_value(rounding=ROUND_DOWN)
    frac = abs_q - full
    # Keep extra precision before rounding so 10/30 bags → exactly 10 kg
    leftover = (frac * uw).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    # Snap tiny float / 3dp truncation noise (9.99→10, 20.01→20)
    nearest = leftover.to_integral_value(rounding=ROUND_HALF_UP)
    if leftover > 0 and abs(leftover - nearest) <= Decimal("0.02"):
        leftover = nearest
    if leftover >= uw:
        add_bags = (leftover / uw).to_integral_value(rounding=ROUND_DOWN)
        full += add_bags
        leftover = (leftover - add_bags * uw).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    return full, leftover, sign


def format_qty_display(qty, unit_weight=None, weight_unit: str | None = "kg") -> str:
    """
    Human-readable qty for weighted products.

    Examples (unit_weight=50 kg):
      49.5    -> 49 + 25 kg
      0.2     -> 10 kg
      2       -> 2
    """
    q = Decimal(str(qty or 0))
    uw = Decimal(str(unit_weight or 0))
    if uw <= 0:
        return _clean_number(q)

    full, leftover, sign = split_qty_units_and_weight(q, uw)
    unit = (weight_unit or "kg").strip() or "kg"
    prefix = "-" if sign < 0 else ""

    if leftover > 0 and full == 0:
        return f"{prefix}{_clean_number(leftover)} {unit}"
    if leftover > 0:
        return f"{prefix}{int(full)} + {_clean_number(leftover)} {unit}"
    return f"{prefix}{int(full)}"


def format_sale_item_qty_display(it, product=None) -> str:
    """
    Qty text for one sale line.

    Full bags → bag count (3), open weight → kg (25 kg), mix on one
    bag-equivalent qty → sealed + open (1 + 25 kg). Never total kg for full bags.
    """
    product = product or getattr(it, "product", None)
    qty = Decimal(str(getattr(it, "quantity", None) or 0))
    sale_weight = getattr(it, "sale_weight", None)
    wu = (
        getattr(it, "weight_unit", None)
        or getattr(product, "weight_unit", None)
        or "kg"
    )
    wu = str(wu).strip() or "kg"
    uw = getattr(product, "unit_weight", None) if product is not None else None

    if product is not None and product_has_weight(product) and uw and Decimal(str(uw)) > 0:
        return format_qty_display(qty, uw, wu)

    if sale_weight is not None and Decimal(str(sale_weight)) > 0:
        return f"{_clean_number(sale_weight)} {wu}"
    if qty != 0:
        return f"{_clean_number(qty)} units"
    return "0"


def format_sale_items_qty_display(items) -> str | None:
    """
    Combined qty for several sale lines of the same product.
    Sums bag-equivalent quantity then formats as sealed + open.
    """
    items = list(items or [])
    if not items:
        return None
    product = getattr(items[0], "product", None)
    total_qty = sum((Decimal(str(getattr(it, "quantity", None) or 0)) for it in items), Decimal("0"))
    uw = getattr(product, "unit_weight", None) if product is not None else None
    wu = (
        getattr(items[0], "weight_unit", None)
        or getattr(product, "weight_unit", None)
        or "kg"
    )
    wu = str(wu).strip() or "kg"
    if product is not None and product_has_weight(product) and uw and Decimal(str(uw)) > 0:
        return format_qty_display(total_qty, uw, wu)
    # Fallback: prefer open weights if present
    total_w = sum(
        (Decimal(str(it.sale_weight)) for it in items if it.sale_weight is not None),
        Decimal("0"),
    )
    if total_w > 0:
        return f"{_clean_number(total_w)} {wu}"
    if total_qty != 0:
        return f"{_clean_number(total_qty)} units"
    return "0"


def format_movement_qty_display(movement, product) -> str:
    """
    Movement qty for weighted products.

    Sealed bags → bag count; open → kg; mix → N + X kg.
    Never collapses full bags into total kg (3×50 must not show as 150 kg).
    """
    uw = getattr(product, "unit_weight", None)
    wu = (getattr(product, "weight_unit", None) or "kg").strip() or "kg"
    qty = Decimal(str(getattr(movement, "quantity", None) or 0))

    if (
        product is not None
        and product_has_weight(product)
        and getattr(movement, "movement_type", None) == "sale_out"
        and getattr(movement, "reference_type", None) == "sale"
        and getattr(movement, "reference_id", None)
    ):
        try:
            from app.models import SaleItem

            items = SaleItem.query.filter_by(
                sale_id=int(movement.reference_id),
                product_id=int(product.id),
            ).all()
            if items:
                text = format_sale_items_qty_display(items)
                if text:
                    prefix = "-" if qty < 0 else ""
                    # Avoid double negative if text somehow signed
                    if text.startswith("-"):
                        return text if qty < 0 else text[1:]
                    return f"{prefix}{text}"
        except Exception:
            pass

    if (
        product is not None
        and product_has_weight(product)
        and getattr(movement, "movement_type", None) == "sale_return_in"
        and getattr(movement, "reference_type", None) == "sale_return"
        and getattr(movement, "reference_id", None)
    ):
        try:
            from app.models import SaleReturnItem

            items = SaleReturnItem.query.filter_by(
                sale_return_id=int(movement.reference_id),
                product_id=int(product.id),
            ).all()
            if items:
                text = format_sale_items_qty_display(items)
                if text:
                    prefix = "+" if qty > 0 else ""
                    if text.startswith("+") or text.startswith("-"):
                        return text
                    return f"{prefix}{text}"
        except Exception:
            pass

    return format_qty_display(
        qty,
        uw if product is not None else None,
        wu if product is not None else "kg",
    )


def stock_pieces_and_leftover(product) -> tuple[Decimal, Decimal]:
    """
    Split fractional stock into whole units + leftover weight.
    leftover_weight = fractional_units * unit_weight.
    """
    full, leftover, _sign = split_qty_units_and_weight(
        getattr(product, "current_stock", 0) or 0,
        getattr(product, "unit_weight", None) or 0,
    )
    return full, leftover


def format_stock_total_display(product) -> str:
    """
    One product = one weight. Uses current_stock (layers − open backorders)
    so negative / pending cover shows as minus, e.g. -1 · -2 + 10 kg.
    """
    stock = Decimal(str(getattr(product, "current_stock", 0) or 0))
    if not product_has_weight(product):
        return _clean_number(stock)

    uw = getattr(product, "unit_weight", None)
    wu = (getattr(product, "weight_unit", None) or "kg").strip() or "kg"
    if uw is not None and Decimal(str(uw or 0)) > 0:
        return format_qty_display(stock, uw, wu)

    # Fallback: physical layers only (no packaging weight)
    from app.models import StockLayer

    layers = StockLayer.query.filter_by(product_id=product.id).all()
    sealed = sum((Decimal(str(L.quantity_remaining or 0)) for L in layers), Decimal("0"))
    open_w = sum(
        (Decimal(str(getattr(L, "open_weight_remaining", None) or 0)) for L in layers),
        Decimal("0"),
    )
    # If stock is negative (pending cover), prefer current_stock over layer-only 0
    if stock < 0:
        return _clean_number(stock)
    unit = wu
    if sealed > 0 and open_w > 0:
        return f"{_clean_number(sealed)} + {_clean_number(open_w)} {unit}"
    if open_w > 0:
        return f"{_clean_number(open_w)} {unit}"
    return _clean_number(sealed)


def format_stock_display(product) -> str:
    """Same as total display — one product weight, sealed + open."""
    return format_stock_total_display(product)


def weight_to_stock_qty(sale_weight, unit_weight) -> Decimal:
    uw = Decimal(str(unit_weight or 0))
    sw = Decimal(str(sale_weight or 0))
    if uw <= 0:
        raise ValueError("Product unit weight is missing.")
    if sw <= 0:
        raise ValueError("Sale weight must be greater than zero.")
    return (sw / uw).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def proportional_sale_amount(list_price, unit_weight, sale_weight) -> Decimal:
    """(list_price / unit_weight) * sale_weight = list_price * (sale_weight/unit_weight)."""
    uw = Decimal(str(unit_weight or 0))
    sw = Decimal(str(sale_weight or 0))
    lp = Decimal(str(list_price or 0))
    if uw <= 0:
        return lp
    amount = lp * (sw / uw)
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

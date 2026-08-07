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


def format_movement_qty_display(movement, product) -> str:
    """
    Movement qty for weighted products.

    Open sales store bag-fraction qty (10 kg ÷ 30 kg bag = 0.333…). Prefer the
    sale line's actual sale_weight so the UI shows -10 kg, not -9.99 kg.
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

            items = (
                SaleItem.query.filter_by(
                    sale_id=int(movement.reference_id),
                    product_id=int(product.id),
                )
                .all()
            )
            weighted = [
                it
                for it in items
                if it.sale_weight is not None and Decimal(str(it.sale_weight)) > 0
            ]
            if len(weighted) == 1:
                sw = Decimal(str(weighted[0].sale_weight))
                unit = (weighted[0].weight_unit or wu).strip() or "kg"
                prefix = "-" if qty < 0 else ""
                return f"{prefix}{_clean_number(sw)} {unit}"
            if len(weighted) > 1 and uw and Decimal(str(uw)) > 0:
                # Multiple open lines: show total open kg for this product on the sale
                total_w = sum((Decimal(str(it.sale_weight)) for it in weighted), Decimal("0"))
                # Only use if bag-equiv roughly matches movement qty
                expected = total_w / Decimal(str(uw))
                if abs(abs(qty) - expected) <= Decimal("0.02"):
                    prefix = "-" if qty < 0 else ""
                    unit = (weighted[0].weight_unit or wu).strip() or "kg"
                    return f"{prefix}{_clean_number(total_w)} {unit}"
        except Exception:
            pass

    if product is not None and product_has_weight(product) and uw and Decimal(str(uw)) > 0:
        # Prefer kg when movement is a partial bag (open sale without sale lookup)
        abs_q = abs(qty)
        if abs_q != abs_q.to_integral_value(rounding=ROUND_DOWN):
            weight = (abs_q * Decimal(str(uw))).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
            nearest = weight.to_integral_value(rounding=ROUND_HALF_UP)
            if abs(weight - nearest) <= Decimal("0.02"):
                weight = nearest
            prefix = "-" if qty < 0 else ""
            return f"{prefix}{_clean_number(weight)} {wu}"

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
    One product = one weight. Show sealed bags + open leftover.
    Examples: 49 + 25 kg · 50 · 9 kg
    """
    from app.models import StockLayer

    layers = StockLayer.query.filter_by(product_id=product.id).all()
    sealed = sum((Decimal(str(L.quantity_remaining or 0)) for L in layers), Decimal("0"))
    open_w = sum(
        (Decimal(str(getattr(L, "open_weight_remaining", None) or 0)) for L in layers),
        Decimal("0"),
    )
    if not product_has_weight(product):
        return _clean_number(Decimal(str(getattr(product, "current_stock", 0) or 0)))

    unit = (getattr(product, "weight_unit", None) or "kg").strip() or "kg"
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

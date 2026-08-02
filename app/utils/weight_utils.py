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


def _clean_number(value: Decimal) -> str:
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text or "0"


def split_qty_units_and_weight(
    qty, unit_weight
) -> tuple[Decimal, Decimal, Decimal]:
    """
    Split stock qty into (whole_units_abs, leftover_weight_abs, sign).
    leftover_weight = fractional_units * unit_weight (always >= 0).
    sign is -1, 0, or 1.
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
    leftover = (frac * uw).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    return full, leftover, sign


def format_qty_display(qty, unit_weight=None, weight_unit: str | None = "kg") -> str:
    """
    Human-readable qty for weighted products.

    Examples (unit_weight=50 kg):
      48.935  -> 48 + 46.75 kg
      1.065   -> 1 + 3.25 kg
      0.065   -> 3.25 kg
      -0.065  -> -3.25 kg
      -1.000  -> -1
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


def format_stock_display(product) -> str:
    if not product_has_weight(product):
        stock = Decimal(str(getattr(product, "current_stock", 0) or 0))
        return _clean_number(stock)
    return format_qty_display(
        getattr(product, "current_stock", 0) or 0,
        getattr(product, "unit_weight", None),
        getattr(product, "weight_unit", None) or "kg",
    )


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

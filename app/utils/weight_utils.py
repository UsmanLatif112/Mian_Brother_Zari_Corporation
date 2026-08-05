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
    Split stock qty into (whole_sealed_bags, open_weight, sign).

    Partial bags are shown as open weight, not as “almost one more bag”.
    Example (unit_weight=50 kg):
      51.5 bags → 50 sealed + 75 kg open   (not 51 + 25 kg)
      50.5 bags → 49 sealed + 75 kg open
      0.5 bags  → 0 sealed + 25 kg open
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
    leftover = (frac * uw).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    # Any open/partial stock means at least one bag was opened — keep that bag
    # in the open-weight side so sealed bags stay whole.
    if leftover > 0 and full >= 1:
        full -= 1
        leftover = (leftover + uw).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    return full, leftover, sign


def format_qty_display(qty, unit_weight=None, weight_unit: str | None = "kg") -> str:
    """
    Human-readable qty for weighted products.

    Examples (unit_weight=50 kg):
      51.5    -> 50 + 75 kg
      50.5    -> 49 + 75 kg
      48.935  -> 48 + 46.75 kg  (after borrow when fractional)
      0.5     -> 25 kg
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
    """
    Stock label: sealed bags + open kg kept separate per packaging size.
    Examples:
      50 sealed + 75 kg open (50kg bags) → "50 + 75 kg"
      Mixed batches → "50×20 kg + 10×100 kg" or with open "49×50 kg + 25 kg"
    """
    from app.models import StockLayer

    layers = (
        StockLayer.query.filter_by(product_id=product.id)
        .order_by(StockLayer.received_at.asc(), StockLayer.id.asc())
        .all()
    )
    stocked = [L for L in layers if L.has_stock()]
    if not stocked:
        stock = Decimal(str(getattr(product, "current_stock", 0) or 0))
        if not product_has_weight(product):
            return _clean_number(stock)
        return format_qty_display(
            stock,
            getattr(product, "unit_weight", None),
            getattr(product, "weight_unit", None) or "kg",
        )

    # Aggregate sealed qty and open weight by packaging
    groups = {}
    order = []
    for layer in stocked:
        if layer.unit_weight is not None and Decimal(str(layer.unit_weight)) > 0:
            uw = Decimal(str(layer.unit_weight))
            wu = (layer.weight_unit or getattr(product, "weight_unit", None) or "kg")
        elif product_has_weight(product):
            uw = Decimal(str(product.unit_weight))
            wu = getattr(product, "weight_unit", None) or "kg"
        else:
            uw = None
            wu = None
        key = (str(uw) if uw is not None else "", wu or "")
        if key not in groups:
            groups[key] = {"sealed": Decimal("0"), "open": Decimal("0")}
            order.append(key)
        groups[key]["sealed"] += Decimal(str(layer.quantity_remaining or 0))
        groups[key]["open"] += Decimal(str(layer.open_weight_remaining or 0))

    parts = []
    for key in order:
        uw_s, wu = key
        sealed = groups[key]["sealed"]
        open_w = groups[key]["open"]
        unit = wu or "kg"
        if not uw_s:
            if sealed > 0:
                parts.append(_clean_number(sealed))
            continue
        uw = Decimal(uw_s)
        if sealed > 0 and open_w > 0:
            if len(order) == 1:
                parts.append(f"{_clean_number(sealed)} + {_clean_number(open_w)} {unit}")
            else:
                parts.append(
                    f"{_clean_number(sealed)}×{_clean_number(uw)} {unit} + {_clean_number(open_w)} {unit}"
                )
        elif sealed > 0:
            if len(order) == 1:
                # Single packaging, whole bags only
                parts.append(_clean_number(sealed))
            else:
                parts.append(f"{_clean_number(sealed)}×{_clean_number(uw)} {unit}")
        elif open_w > 0:
            parts.append(f"{_clean_number(open_w)} {unit}")
    return " + ".join(parts) if parts else "0"


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

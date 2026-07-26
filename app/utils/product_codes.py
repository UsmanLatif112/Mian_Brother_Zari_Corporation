"""Generate unique product SKU and barcode codes."""

from __future__ import annotations

from app.extensions import db
from app.models import Product


def _next_sequence() -> int:
    """Next free numeric sequence based on highest product id."""
    current = db.session.query(db.func.max(Product.id)).scalar()
    return int(current or 0) + 1


def generate_unique_sku() -> str:
    seq = _next_sequence()
    for _ in range(5000):
        candidate = f"SKU-{seq:06d}"
        exists = Product.query.filter_by(sku=candidate).first()
        if not exists:
            return candidate
        seq += 1
    raise RuntimeError("Could not generate a unique SKU.")


def generate_unique_barcode() -> str:
    """Internal 13-digit barcode (prefix 2 = in-store / non-GS1)."""
    seq = _next_sequence()
    for _ in range(5000):
        body = f"{seq:012d}"[-12:]
        candidate = f"2{body}"
        exists = Product.query.filter_by(barcode=candidate).first()
        if not exists:
            return candidate
        seq += 1
    raise RuntimeError("Could not generate a unique barcode.")


def ensure_product_codes(sku: str | None, barcode: str | None) -> tuple[str, str]:
    """
    Return SKU + barcode, auto-filling either when blank.
    User-provided values are kept as-is (trimmed).
    """
    sku_clean = (sku or "").strip()
    barcode_clean = (barcode or "").strip()
    if not sku_clean:
        sku_clean = generate_unique_sku()
    if not barcode_clean:
        barcode_clean = generate_unique_barcode()
    return sku_clean, barcode_clean

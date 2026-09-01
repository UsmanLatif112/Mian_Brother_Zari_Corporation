"""Labels and badges for inventory stock movement rows."""


def movement_badge(movement) -> dict:
    """Return {label, css} for Recent Movements type column."""
    mt = (getattr(movement, "movement_type", None) or "").strip()
    rt = (getattr(movement, "reference_type", None) or "").strip()
    notes = (getattr(movement, "notes", None) or "").strip()

    if mt == "sale_void_in" or (mt == "sale_return_in" and rt == "sale_void"):
        return {"label": "Void Reversal", "css": "bg-warning-subtle text-warning border"}
    if mt == "sale_return_in":
        return {"label": "Sale Return In", "css": "bg-success-subtle text-success border"}
    if mt == "sale_out":
        return {"label": "Sale Out", "css": "bg-danger-subtle text-danger border"}
    if mt == "purchase_in":
        return {"label": "Purchase In", "css": "bg-primary-subtle text-primary border"}
    if mt == "reprice":
        return {"label": "Reprice", "css": "bg-info-subtle text-info border"}
    if mt == "adjust_increase":
        return {"label": "Qty Increase", "css": "bg-warning-subtle text-warning border"}
    if mt == "adjust_decrease":
        return {"label": "Qty Decrease", "css": "bg-warning-subtle text-warning border"}
    if mt.startswith("adjust_"):
        return {"label": mt.replace("_", " ").title(), "css": "bg-warning-subtle text-warning border"}

    # Legacy rows: void stored as sale_return before migration
    if mt == "sale_return_in" and notes.lower().startswith("void sale"):
        return {"label": "Void Reversal", "css": "bg-warning-subtle text-warning border"}

    return {"label": mt.replace("_", " ").title() or "Movement", "css": "bg-secondary-subtle text-secondary border"}


def format_purchase_movement_notes(sealed, open_weight, unit_weight, weight_unit, extra=None) -> str:
    """Human-readable purchase line for movement notes."""
    from decimal import Decimal

    sealed = Decimal(str(sealed or 0))
    open_w = Decimal(str(open_weight or 0))
    uw = Decimal(str(unit_weight or 0)) if unit_weight else Decimal("0")
    wu = (weight_unit or "kg").strip() or "kg"
    parts = []
    if sealed > 0:
        n = int(sealed) if sealed == sealed.to_integral_value() else float(sealed)
        parts.append(f"{n} sealed bag{'s' if sealed != 1 else ''}")
    if open_w > 0:
        parts.append(f"{open_w.normalize()} {wu} loose")
    if not parts:
        parts.append("stock in")
    base = "Purchase: " + " + ".join(parts)
    if uw > 0 and sealed > 0 and open_w > 0:
        total = sealed + open_w / uw
        base += f" ({total.normalize()} bag equiv @ {uw.normalize()} {wu}/bag)"
    extra = (extra or "").strip()
    if extra and extra.lower() not in ("inventory add",):
        return f"{base}; {extra}"
    return base

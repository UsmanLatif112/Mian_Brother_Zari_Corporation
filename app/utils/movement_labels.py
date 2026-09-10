"""Labels and badges for inventory stock movement rows."""

import re
from decimal import Decimal
from types import SimpleNamespace


def _sale_line_particulars(product, quantity, *, sale_weight=None, weight_unit=None) -> str:
    """Product name / qty text for movement notes (matches journal sale lines)."""
    from app.services.journal_service import _sale_line_particular

    stub = SimpleNamespace(
        product=product,
        quantity=quantity,
        sale_weight=sale_weight,
        weight_unit=weight_unit,
    )
    return _sale_line_particular(stub)


def sale_restore_movement_notes(invoice_no, product, item, *, for_edit: bool) -> str:
    """Notes when stock is restored from voiding or editing a sale line."""
    parts = _sale_line_particulars(
        product,
        getattr(item, "quantity", None),
        sale_weight=getattr(item, "sale_weight", None),
        weight_unit=getattr(item, "weight_unit", None),
    )
    inv = (invoice_no or "").strip() or "—"
    if for_edit:
        return f"Sale edit {inv} — prior qty restored / {parts}"
    return f"Void sale {inv} / {parts}"


def sale_out_movement_notes(invoice_no, product, quantity, *, sale_weight=None, weight_unit=None, is_edit=False) -> str:
    """Notes when stock leaves on a sale line."""
    parts = _sale_line_particulars(
        product,
        quantity,
        sale_weight=sale_weight,
        weight_unit=weight_unit,
    )
    inv = (invoice_no or "").strip() or "—"
    if is_edit:
        return f"Sale edit {inv} — new qty / {parts}"
    return f"Sale {inv} / {parts}"


def movement_badge(movement) -> dict:
    """Return {label, css} for Recent Movements type column."""
    mt = (getattr(movement, "movement_type", None) or "").strip()
    rt = (getattr(movement, "reference_type", None) or "").strip()
    notes = (getattr(movement, "notes", None) or "").strip()
    notes_l = notes.lower()

    if mt == "sale_void_in" and rt == "sale_edit":
        return {"label": "Sale Edit", "css": "bg-info-subtle text-info border"}
    if mt == "sale_void_in" or (mt == "sale_return_in" and rt == "sale_void"):
        return {"label": "Void Reversal", "css": "bg-warning-subtle text-warning border"}
    if mt == "sale_out" and notes_l.startswith("sale edit"):
        return {"label": "Sale Edit", "css": "bg-info-subtle text-info border"}
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


def movement_particulars(movement) -> str:
    """Human-readable particulars for Recent Movements (notes are pre-formatted)."""
    notes = (getattr(movement, "notes", None) or "").strip()
    if notes:
        return notes
    mt = (getattr(movement, "movement_type", None) or "").strip()
    return mt.replace("_", " ").title() or "—"


def _sale_edit_invoice(notes: str) -> str | None:
    match = re.search(r"Sale edit\s+(INV-\S+)", notes or "", re.I)
    return match.group(1) if match else None


def _is_sale_edit_restore_row(movement) -> bool:
    rt = (getattr(movement, "reference_type", None) or "").strip()
    mt = (getattr(movement, "movement_type", None) or "").strip()
    notes_l = (getattr(movement, "notes", None) or "").lower()
    return mt == "sale_void_in" and rt == "sale_edit" and "prior qty restored" in notes_l


def _is_sale_edit_out_row(movement) -> bool:
    mt = (getattr(movement, "movement_type", None) or "").strip()
    notes_l = (getattr(movement, "notes", None) or "").lower()
    return mt == "sale_out" and notes_l.startswith("sale edit") and "new qty" in notes_l


def _merge_sale_edit_particulars(invoice_no: str, restore_notes: str, out_notes: str) -> str:
    def _line_tail(notes: str) -> str:
        match = re.search(r"(?:prior qty restored|new qty)\s*/\s*(.+)$", notes or "", re.I)
        return (match.group(1) or "").strip()

    old_line = _line_tail(restore_notes)
    new_line = _line_tail(out_notes)
    inv = (invoice_no or "").strip() or "—"
    if not old_line or not new_line:
        return f"Sale edit {inv} — qty adjusted"
    old_parts = [part.strip() for part in old_line.split("/", 1)]
    new_parts = [part.strip() for part in new_line.split("/", 1)]
    if len(old_parts) == 2 and len(new_parts) == 2 and old_parts[0] == new_parts[0]:
        return f"Sale edit {inv} / {old_parts[0]} / {old_parts[1]} → {new_parts[1]}"
    return f"Sale edit {inv} / {old_line} → {new_line}"


def group_sale_edit_movements(movements):
    """Show one Recent Movements row per sale-edit qty change (restore + deduct pair)."""
    rows = list(movements)
    skip_ids: set[int] = set()
    grouped = []

    for i, movement in enumerate(rows):
        movement_id = getattr(movement, "id", None)
        if movement_id in skip_ids:
            continue

        if not (_is_sale_edit_restore_row(movement) or _is_sale_edit_out_row(movement)):
            grouped.append(movement)
            continue

        invoice_no = _sale_edit_invoice(getattr(movement, "notes", "") or "")
        if not invoice_no:
            grouped.append(movement)
            continue

        partner = None
        for j in range(i + 1, min(i + 6, len(rows))):
            candidate = rows[j]
            candidate_id = getattr(candidate, "id", None)
            if candidate_id in skip_ids:
                continue
            if _sale_edit_invoice(getattr(candidate, "notes", "") or "") != invoice_no:
                continue
            if _is_sale_edit_restore_row(movement) and _is_sale_edit_out_row(candidate):
                partner = candidate
                break
            if _is_sale_edit_out_row(movement) and _is_sale_edit_restore_row(candidate):
                partner = candidate
                break

        if partner is None:
            grouped.append(movement)
            continue

        skip_ids.add(movement_id)
        skip_ids.add(getattr(partner, "id", None))

        restore = movement if _is_sale_edit_restore_row(movement) else partner
        out = partner if restore is movement else movement
        net_qty = Decimal(str(restore.quantity or 0)) + Decimal(str(out.quantity or 0))

        grouped.append(
            SimpleNamespace(
                id=getattr(out, "id", None) or movement_id,
                created_at=getattr(out, "created_at", None) or getattr(movement, "created_at", None),
                movement_type="sale_out",
                reference_type="sale_edit",
                reference_id=getattr(out, "reference_id", None) or getattr(movement, "reference_id", None),
                quantity=net_qty,
                unit_cost=getattr(out, "unit_cost", None) or getattr(movement, "unit_cost", None),
                balance_after=getattr(out, "balance_after", None),
                notes=_merge_sale_edit_particulars(invoice_no, restore.notes, out.notes),
                product_id=getattr(movement, "product_id", None),
                product=getattr(movement, "product", None),
                created_by=getattr(out, "created_by", None) or getattr(movement, "created_by", None),
                created_by_id=getattr(out, "created_by_id", None) or getattr(movement, "created_by_id", None),
            )
        )

    return grouped


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

"""Record purchases (inventory receipts) with stock, vendor balance, and ledger."""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models import LedgerEntry, Purchase, PurchaseItem, Vendor
from app.services.fifo_service import fifo_receive
from app.services.ledger_service import (
    delete_ledger_by_reference,
    post_ledger_entry,
    rebuild_party_balances,
)
from app.utils.working_date import get_working_date


def _fmt_qty(value) -> str:
    q = Decimal(str(value or 0))
    text = f"{q:.3f}".rstrip("0").rstrip(".")
    return text or "0"


def purchase_items_summary(purchase) -> str:
    """Human-readable list of what was purchased (for vendor ledger notes)."""
    if not purchase:
        return ""
    parts = []
    for it in purchase.items or []:
        name = it.product.name if it.product else f"Product #{it.product_id}"
        qty = _fmt_qty(it.quantity)
        price = f"{Decimal(str(it.unit_price or 0)):.2f}"
        parts.append(f"{name} × {qty} @ {price}")
    if parts:
        return "; ".join(parts)
    inv = (purchase.invoice_no or "").strip()
    return f"Purchase {inv}" if inv else "Purchase"


def record_purchase(
    vendor_id,
    items,
    user_id,
    invoice_no=None,
    purchase_date=None,
    discount=None,
    tax_amount=None,
    transport_charges=None,
    notes=None,
):
    """
    Create a Purchase with line items, receive stock (FIFO), update vendor
    payable balance, and post a vendor ledger entry.

    items: list of dicts with keys:
      product, quantity, unit_price, sale_price=None,
      batch_number=None, expiry_date=None
    """
    vendor = db.session.get(Vendor, vendor_id)
    if not vendor or vendor.is_deleted:
        raise ValueError("Vendor is required.")
    if not items:
        raise ValueError("Add at least one product line.")

    purchase_date = purchase_date or get_working_date()
    discount = Decimal(str(discount or 0))
    tax_amount = Decimal(str(tax_amount or 0))
    transport_charges = Decimal(str(transport_charges or 0))
    invoice_no = (invoice_no or "").strip() or None

    purchase = Purchase(
        invoice_no=invoice_no or "PO-TEMP",
        vendor_id=vendor.id,
        purchase_date=purchase_date,
        discount=discount,
        tax_amount=tax_amount,
        transport_charges=transport_charges,
        notes=notes,
        created_by_id=user_id,
    )
    db.session.add(purchase)
    db.session.flush()
    if not invoice_no:
        purchase.invoice_no = f"PO-{purchase.id:06d}"

    subtotal = Decimal("0")
    for line in items:
        product = line["product"]
        qty = Decimal(str(line["quantity"]))
        price = Decimal(str(line["unit_price"]))
        if qty <= 0:
            raise ValueError(f"Quantity must be positive for {product.name}.")
        sale_price = line.get("sale_price")
        if sale_price is not None and sale_price != "":
            batch_sale = Decimal(str(sale_price))
        else:
            batch_sale = Decimal(str(product.sale_price or 0))
        line_total = qty * price
        db.session.add(
            PurchaseItem(
                purchase_id=purchase.id,
                product_id=product.id,
                quantity=qty,
                unit_price=price,
                line_total=line_total,
            )
        )
        fifo_receive(
            product,
            qty,
            price,
            "purchase",
            purchase.id,
            user_id,
            sale_price=batch_sale,
            batch_number=line.get("batch_number"),
            expiry_date=line.get("expiry_date"),
            vendor_id=vendor.id,
            invoice_no=purchase.invoice_no,
            notes=notes,
            entry_at=purchase_date,
        )
        subtotal += line_total

    purchase.subtotal = subtotal
    purchase.grand_total = subtotal - discount + tax_amount + transport_charges

    # We owe the vendor more (payable increases)
    vendor.balance = Decimal(str(vendor.balance or 0)) + purchase.grand_total
    db.session.flush()
    post_ledger_entry(
        "vendor",
        vendor.id,
        "purchase",
        debit=purchase.grand_total,
        credit=Decimal("0"),
        entry_date=purchase.purchase_date,
        reference_type="purchase",
        reference_id=purchase.id,
        notes=purchase_items_summary(purchase),
    )
    return purchase


def _find_purchase_item_for_layer(purchase, product_id, old_purchased):
    """Best-effort match of PurchaseItem for a stock layer on this purchase."""
    items = [
        it
        for it in (purchase.items or [])
        if int(it.product_id) == int(product_id)
    ]
    if not items:
        return None
    if len(items) == 1:
        return items[0]
    old_purchased = Decimal(str(old_purchased or 0))
    for it in items:
        if Decimal(str(it.quantity or 0)) == old_purchased:
            return it
    return items[0]


def _recompute_purchase_totals(purchase):
    subtotal = sum(
        (Decimal(str(it.line_total or 0)) for it in (purchase.items or [])),
        Decimal("0"),
    )
    discount = Decimal(str(purchase.discount or 0))
    tax_amount = Decimal(str(purchase.tax_amount or 0))
    transport = Decimal(str(purchase.transport_charges or 0))
    purchase.subtotal = subtotal
    purchase.grand_total = subtotal - discount + tax_amount + transport
    return purchase.grand_total


def _sync_purchase_vendor_ledger(purchase, old_vendor_id, notes=None):
    """Keep the purchase ledger row and vendor.balance aligned with purchase.grand_total."""
    grand = Decimal(str(purchase.grand_total or 0))
    new_vendor_id = int(purchase.vendor_id)
    old_vendor_id = int(old_vendor_id) if old_vendor_id else new_vendor_id
    ledger_notes = notes or purchase_items_summary(purchase)

    ledger = (
        LedgerEntry.query.filter_by(
            party_type="vendor",
            reference_type="purchase",
            reference_id=purchase.id,
            entry_type="purchase",
        )
        .order_by(LedgerEntry.id.asc())
        .first()
    )

    parties = {old_vendor_id, new_vendor_id}
    if ledger:
        ledger.party_id = new_vendor_id
        ledger.debit = grand
        ledger.credit = Decimal("0")
        ledger.notes = ledger_notes
        db.session.flush()
        for vid in parties:
            rebuild_party_balances("vendor", vid)
        return ledger

    post_ledger_entry(
        "vendor",
        new_vendor_id,
        "purchase",
        debit=grand,
        credit=Decimal("0"),
        entry_date=purchase.purchase_date,
        reference_type="purchase",
        reference_id=purchase.id,
        notes=ledger_notes,
    )
    rebuild_party_balances("vendor", new_vendor_id)
    if old_vendor_id != new_vendor_id:
        rebuild_party_balances("vendor", old_vendor_id)
    return None


def reverse_purchase_for_layer(layer, *, notes=None):
    """
    When a purchase-backed stock batch is deleted/cleared, reduce the linked
    Purchase by the *unsold remaining* qty only (sold qty stays on purchase /
    vendor payable). Rebuilds vendor ledger + balance.

    Returns dict with purchase_id, invoice_no, vendor_id, deleted (bool)
    or None if nothing to reverse.
    """
    if not layer or (layer.source_type or "") != "purchase" or not layer.source_id:
        return None

    purchase = db.session.get(Purchase, int(layer.source_id))
    if not purchase:
        return None

    received = Decimal(
        str(
            layer.quantity_received
            if layer.quantity_received is not None
            else layer.quantity_remaining
            or 0
        )
    )
    remaining = Decimal(str(layer.quantity_remaining or 0))
    if remaining < 0:
        remaining = Decimal("0")
    # Only reverse what is still in stock (unsold). Sold portion stays payable.
    unsold = remaining
    sold = received - remaining if received > remaining else Decimal("0")
    if unsold <= 0:
        return None

    old_purchased = received if received > 0 else unsold
    item = _find_purchase_item_for_layer(purchase, layer.product_id, old_purchased)
    if not item:
        return None

    vendor_id = int(purchase.vendor_id)
    purchase_id = purchase.id
    invoice = purchase.invoice_no
    unit_cost = Decimal(str(item.unit_price or layer.unit_cost or 0))
    new_purchased = sold  # keep sold qty on the purchase document

    if new_purchased > 0:
        item.quantity = new_purchased
        item.unit_price = unit_cost
        item.line_total = new_purchased * unit_cost
        db.session.flush()
        _recompute_purchase_totals(purchase)
        _sync_purchase_vendor_ledger(
            purchase,
            vendor_id,
            notes=notes or purchase_items_summary(purchase),
        )
        return {
            "purchase_id": purchase_id,
            "invoice_no": invoice,
            "vendor_id": vendor_id,
            "deleted": False,
            "reversed_qty": float(unsold),
        }

    # Entire purchased qty still in stock → remove this purchase line (or purchase).
    remaining_items = [
        it
        for it in (purchase.items or [])
        if it is not item and Decimal(str(it.quantity or 0)) > 0
    ]
    if remaining_items:
        item.quantity = Decimal("0")
        item.line_total = Decimal("0")
        db.session.flush()
        _recompute_purchase_totals(purchase)
        _sync_purchase_vendor_ledger(
            purchase,
            vendor_id,
            notes=notes or purchase_items_summary(purchase),
        )
        return {
            "purchase_id": purchase_id,
            "invoice_no": invoice,
            "vendor_id": vendor_id,
            "deleted": False,
            "reversed_qty": float(unsold),
        }

    db.session.delete(item)
    db.session.flush()
    delete_ledger_by_reference("purchase", purchase.id, rebuild=False)
    db.session.delete(purchase)
    db.session.flush()
    rebuild_party_balances("vendor", vendor_id)
    return {
        "purchase_id": purchase_id,
        "invoice_no": invoice,
        "vendor_id": vendor_id,
        "deleted": True,
        "reversed_qty": float(unsold),
    }


def void_purchase(purchase_id, user_id=None, *, notes=None):
    """
    Reverse a purchase from ledger/inventory: clear unsold stock, reduce or
    remove the Purchase document, and rebuild vendor payable. Sold qty stays
    on the purchase (void related sales first to reverse those).
    """
    from sqlalchemy import func

    from app.models import Product, StockLayer
    from app.services.fifo_service import adjust_batch

    purchase = db.session.get(Purchase, int(purchase_id))
    if not purchase:
        raise ValueError("Purchase not found.")

    vendor_id = int(purchase.vendor_id)
    invoice = purchase.invoice_no
    pid = purchase.id
    note = notes or f"Void purchase {invoice}"

    layers = (
        StockLayer.query.filter_by(source_type="purchase", source_id=pid)
        .order_by(StockLayer.id)
        .all()
    )
    total_remaining = sum(
        (Decimal(str(layer.quantity_remaining or 0)) for layer in layers),
        Decimal("0"),
    )

    if not layers:
        delete_ledger_by_reference("purchase", pid, rebuild=False)
        for item in list(purchase.items or []):
            db.session.delete(item)
        db.session.delete(purchase)
        db.session.flush()
        rebuild_party_balances("vendor", vendor_id)
        return {
            "purchase_id": pid,
            "invoice_no": invoice,
            "vendor_id": vendor_id,
            "deleted": True,
            "reversed": True,
        }

    if total_remaining <= 0:
        raise ValueError(
            "Cannot reverse this purchase: all stock was already sold. "
            "Void related sales first, or clear remaining batches from Inventory."
        )

    product_ids = set()
    reversed_any = False
    for layer in layers:
        product_ids.add(layer.product_id)
        remaining = Decimal(str(layer.quantity_remaining or 0))
        if remaining <= 0:
            continue
        info = reverse_purchase_for_layer(layer, notes=note)
        if info:
            reversed_any = True
        adjust_batch(layer, Decimal("0"), user_id, notes=note)
        db.session.delete(layer)

    db.session.flush()

    for product_id in product_ids:
        product = db.session.get(Product, product_id)
        if not product:
            continue
        open_qty = (
            db.session.query(func.coalesce(func.sum(StockLayer.quantity_remaining), 0))
            .filter(StockLayer.product_id == product_id)
            .scalar()
        )
        product.current_stock = Decimal(str(open_qty or 0))

    still = db.session.get(Purchase, pid)
    return {
        "purchase_id": pid,
        "invoice_no": invoice,
        "vendor_id": vendor_id,
        "deleted": still is None,
        "reversed": reversed_any or still is None,
    }


def amend_purchase_for_layer(
    layer,
    *,
    old_purchased,
    new_purchased,
    old_unit_cost,
    new_unit_cost,
    new_vendor_id=None,
    new_invoice_no=None,
    notes=None,
):
    """
    When a purchase-backed stock batch is edited, keep Purchase / PurchaseItem
    and the vendor ledger / payable in sync so purchasing totals match stock.
    """
    if not layer or (layer.source_type or "") != "purchase" or not layer.source_id:
        return None

    purchase = db.session.get(Purchase, int(layer.source_id))
    if not purchase:
        return None

    item = _find_purchase_item_for_layer(purchase, layer.product_id, old_purchased)
    if not item:
        return None

    old_purchased = Decimal(str(old_purchased or 0))
    new_purchased = Decimal(str(new_purchased or 0))
    old_unit_cost = Decimal(str(old_unit_cost or 0))
    new_unit_cost = Decimal(
        str(new_unit_cost if new_unit_cost is not None else old_unit_cost)
    )
    if new_purchased < 0:
        raise ValueError("Purchased quantity cannot be negative.")

    # No-op if nothing that affects purchasing changed
    same_qty = old_purchased == new_purchased
    same_cost = old_unit_cost == new_unit_cost
    same_vendor = (
        new_vendor_id is None or int(new_vendor_id) == int(purchase.vendor_id)
    )
    same_invoice = new_invoice_no is None or (
        (str(new_invoice_no).strip() or None) == (purchase.invoice_no or None)
    )
    if same_qty and same_cost and same_vendor and same_invoice:
        return purchase

    old_vendor_id = purchase.vendor_id
    if new_vendor_id:
        vendor = db.session.get(Vendor, int(new_vendor_id))
        if not vendor or vendor.is_deleted:
            raise ValueError("Vendor is required.")
        other_products = {
            int(it.product_id)
            for it in (purchase.items or [])
            if int(it.product_id) != int(layer.product_id)
        }
        if not other_products or int(purchase.vendor_id) == int(new_vendor_id):
            purchase.vendor_id = vendor.id

    if new_invoice_no is not None:
        inv = str(new_invoice_no).strip() or None
        if inv:
            purchase.invoice_no = inv

    item.quantity = new_purchased
    item.unit_price = new_unit_cost
    item.line_total = new_purchased * new_unit_cost
    _recompute_purchase_totals(purchase)

    # Always refresh ledger notes from line items (what was purchased)
    _sync_purchase_vendor_ledger(purchase, old_vendor_id, notes=None)
    return purchase

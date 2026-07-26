"""Record purchases (inventory receipts) with stock, vendor balance, and ledger."""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models import LedgerEntry, Purchase, PurchaseItem, Vendor
from app.services.fifo_service import fifo_receive
from app.services.ledger_service import post_ledger_entry, rebuild_party_balances


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

    purchase_date = purchase_date or date.today()
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

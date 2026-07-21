"""Record purchases (inventory receipts) with stock, vendor balance, and ledger."""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models import Purchase, PurchaseItem, Vendor
from app.services.fifo_service import fifo_receive
from app.services.ledger_service import post_ledger_entry


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
    post_ledger_entry(
        "vendor",
        vendor.id,
        "purchase",
        debit=purchase.grand_total,
        credit=Decimal("0"),
        entry_date=purchase.purchase_date,
        reference_type="purchase",
        reference_id=purchase.id,
        notes=notes or f"Purchase {purchase.invoice_no}",
    )
    return purchase

"""Customer lifecycle operations (purge with related documents)."""

from decimal import Decimal

from app.extensions import db
from app.models import Customer, CustomerReceiving, LedgerEntry, Sale
from app.models.mixins import utcnow
from app.services.customer_payment_service import delete_customer_payment
from app.services.ledger_service import rebuild_party_balances
from app.services.sale_service import void_sale
from app.services.sync_service import enqueue_sync
from app.utils.uploads import delete_image

# Flip to True to allow customer delete in UI + API again.
CUSTOMER_DELETE_ENABLED = False
CUSTOMER_DELETE_DISABLED_HINT = "Contact with admin"


def delete_customer_cascade(customer_id, user_id=None):
    """
    Soft-delete a customer and permanently remove related business data:
    sales (stock/cash reverse), payments/receivings, remaining ledger rows.
    """
    if not CUSTOMER_DELETE_ENABLED:
        raise ValueError(CUSTOMER_DELETE_DISABLED_HINT)

    customer = db.session.get(Customer, customer_id)
    if not customer or customer.is_deleted:
        raise ValueError("Customer not found.")

    cid = customer.id
    name = customer.name

    # Sales first — restores stock and reverses sale cash / sale ledger lines
    sale_ids = [
        row.id
        for row in Sale.query.filter_by(customer_id=cid).order_by(Sale.id.asc()).all()
    ]
    for sale_id in sale_ids:
        void_sale(sale_id, user_id)

    # Payments / receivings — reverse cash + ledger for each payment
    receiving_ids = [
        row.id
        for row in CustomerReceiving.query.filter_by(customer_id=cid)
        .order_by(CustomerReceiving.id.asc())
        .all()
    ]
    for receiving_id in receiving_ids:
        delete_customer_payment(receiving_id, user_id)

    # Opening balance and any leftover ledger lines for this party
    leftovers = (
        LedgerEntry.query.filter_by(party_type="customer", party_id=cid).all()
    )
    for entry in leftovers:
        db.session.delete(entry)
    db.session.flush()

    customer.is_deleted = True
    customer.deleted_at = utcnow()
    customer.opening_balance = Decimal("0")
    if customer.photo:
        delete_image(customer.photo)
        customer.photo = None

    rebuild_party_balances("customer", cid)
    enqueue_sync("customers", cid, "delete")
    return name

from datetime import date
from decimal import Decimal

from flask_login import current_user

from app.extensions import db
from app.models import Customer, CustomerReceiving
from app.models.mixins import utcnow
from app.models.sales import PaymentMethod
from app.services.cashbook_service import record_cash_movement, reverse_cash_by_reference
from app.services.ledger_service import delete_ledger_by_reference, post_ledger_entry, rebuild_party_balances
from app.services.sync_service import enqueue_sync

PAYMENT_TYPES = {
    "advance": "Advance",
    "loan": "Loan",
    "account_settle": "Account Settle",
}


def record_customer_payment(
    customer_id,
    payment_type,
    amount,
    payment_date=None,
    remarks=None,
    user_id=None,
):
    """
    Record customer cash movement:
    - advance: customer pays ahead → cash in, balance decreases (advance with us)
    - account_settle: customer pays against credit → cash in, balance decreases
    - loan: customer borrows cash → cash out, balance increases (owes us)
    """
    customer = db.session.get(Customer, customer_id)
    if not customer or customer.is_deleted:
        raise ValueError("Customer not found.")

    ptype = (payment_type or "").strip().lower()
    if ptype not in PAYMENT_TYPES:
        raise ValueError("Invalid payment type. Use advance, loan, or account_settle.")

    amount = Decimal(str(amount or 0))
    if amount <= 0:
        raise ValueError("Amount must be greater than zero.")

    entry_date = payment_date or date.today()
    if isinstance(entry_date, str):
        entry_date = date.fromisoformat(entry_date)

    uid = user_id or (current_user.id if current_user and current_user.is_authenticated else None)
    label = PAYMENT_TYPES[ptype]
    note = (remarks or "").strip() or None

    receiving = CustomerReceiving(
        customer_id=customer.id,
        receiving_date=entry_date,
        amount=amount,
        payment_type=ptype,
        payment_method=PaymentMethod.CASH,
        notes=note,
        created_by_id=uid,
        created_at=utcnow(),
    )
    db.session.add(receiving)
    db.session.flush()

    bal = Decimal(str(customer.balance or 0))
    if ptype == "loan":
        # We give cash → customer owes more
        customer.balance = bal + amount
        post_ledger_entry(
            "customer",
            customer.id,
            "loan",
            debit=amount,
            credit=Decimal("0"),
            entry_date=entry_date,
            reference_type="customer_receiving",
            reference_id=receiving.id,
            notes=note or "Cash loan to customer",
        )
        record_cash_movement(
            "out",
            "customer_loan",
            amount,
            "customer_receiving",
            receiving.id,
            notes=f"Loan to {customer.name}" + (f" — {note}" if note else ""),
            created_by_id=uid,
            entry_date=entry_date,
        )
    else:
        # advance or account settle → cash in, reduce what they owe / increase advance
        customer.balance = bal - amount
        post_ledger_entry(
            "customer",
            customer.id,
            ptype,
            debit=Decimal("0"),
            credit=amount,
            entry_date=entry_date,
            reference_type="customer_receiving",
            reference_id=receiving.id,
            notes=note or label,
        )
        category = "customer_advance" if ptype == "advance" else "customer_settle"
        record_cash_movement(
            "in",
            category,
            amount,
            "customer_receiving",
            receiving.id,
            notes=f"{label} from {customer.name}" + (f" — {note}" if note else ""),
            created_by_id=uid,
            entry_date=entry_date,
        )

    enqueue_sync("customer_receivings", receiving.id, "create")
    enqueue_sync("customers", customer.id, "update")
    return receiving


def delete_customer_payment(receiving_id, user_id=None):
    receiving = db.session.get(CustomerReceiving, receiving_id)
    if not receiving:
        raise ValueError("Payment not found.")
    customer_id = receiving.customer_id
    reverse_cash_by_reference(
        "customer_receiving",
        receiving.id,
        notes=f"Void payment #{receiving.id}",
        created_by_id=user_id,
    )
    delete_ledger_by_reference("customer_receiving", receiving.id, rebuild=False)
    db.session.delete(receiving)
    db.session.flush()
    rebuild_party_balances("customer", customer_id)
    enqueue_sync("customer_receivings", receiving_id, "delete")
    enqueue_sync("customers", customer_id, "update")
    return customer_id

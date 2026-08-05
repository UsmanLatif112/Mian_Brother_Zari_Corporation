from datetime import date
from decimal import Decimal

from flask_login import current_user

from app.extensions import db
from app.models import Vendor, VendorPayment
from app.models.mixins import utcnow
from app.models.sales import PaymentMethod
from app.services.cashbook_service import record_cash_movement, reverse_cash_by_reference
from app.services.ledger_service import delete_ledger_by_reference, post_ledger_entry, rebuild_party_balances
from app.services.sync_service import enqueue_sync
from app.utils.working_date import get_working_date

PAYMENT_TYPES = {
    "advance": "Advance",
    "loan": "Loan",
    "account_settle": "Account Settle",
}


def record_vendor_payment(
    vendor_id,
    payment_type,
    amount,
    payment_date=None,
    remarks=None,
    user_id=None,
):
    """
    Record vendor cash movement.
    Vendor balance > 0 means we owe the vendor.

    - account_settle: we pay against payable → cash out, balance decreases
    - advance: we pay ahead → cash out, balance decreases (prepaid)
    - loan: vendor gives us cash → cash in, balance increases (we owe more)
    """
    vendor = db.session.get(Vendor, vendor_id)
    if not vendor or vendor.is_deleted:
        raise ValueError("Vendor not found.")

    ptype = (payment_type or "").strip().lower()
    if ptype not in PAYMENT_TYPES:
        raise ValueError("Invalid payment type. Use advance, loan, or account_settle.")

    amount = Decimal(str(amount or 0))
    if amount <= 0:
        raise ValueError("Amount must be greater than zero.")

    entry_date = payment_date or get_working_date()
    if isinstance(entry_date, str):
        entry_date = date.fromisoformat(entry_date)

    uid = user_id or (current_user.id if current_user and current_user.is_authenticated else None)
    label = PAYMENT_TYPES[ptype]
    note = (remarks or "").strip() or None

    payment = VendorPayment(
        vendor_id=vendor.id,
        payment_date=entry_date,
        amount=amount,
        payment_type=ptype,
        payment_method=PaymentMethod.CASH,
        notes=note,
        created_by_id=uid,
        created_at=utcnow(),
    )
    db.session.add(payment)
    db.session.flush()

    bal = Decimal(str(vendor.balance or 0))
    if ptype == "loan":
        # Vendor gives us cash → we owe more
        vendor.balance = bal + amount
        post_ledger_entry(
            "vendor",
            vendor.id,
            "loan",
            debit=amount,
            credit=Decimal("0"),
            entry_date=entry_date,
            reference_type="vendor_payment",
            reference_id=payment.id,
            notes=note or "Cash loan from vendor",
        )
        record_cash_movement(
            "in",
            "vendor_loan",
            amount,
            "vendor_payment",
            payment.id,
            notes=f"Loan from {vendor.name}" + (f" — {note}" if note else ""),
            created_by_id=uid,
            entry_date=entry_date,
        )
    else:
        # advance or account settle → we pay vendor, reduce payable / increase prepaid
        vendor.balance = bal - amount
        post_ledger_entry(
            "vendor",
            vendor.id,
            ptype,
            debit=Decimal("0"),
            credit=amount,
            entry_date=entry_date,
            reference_type="vendor_payment",
            reference_id=payment.id,
            notes=note or label,
        )
        # Paying vendor does not change shop cash-in-hand / cash book

    enqueue_sync("vendor_payments", payment.id, "create")
    enqueue_sync("vendors", vendor.id, "update")
    return payment


def delete_vendor_payment(payment_id, user_id=None):
    """Delete vendor payment and reverse ledger (+ cash for loan inflows)."""
    payment = db.session.get(VendorPayment, payment_id)
    if not payment:
        raise ValueError("Payment not found.")

    vendor_id = payment.vendor_id
    ptype = (payment.payment_type or "").strip().lower()

    if ptype == "loan":
        reverse_cash_by_reference(
            "vendor_payment",
            payment.id,
            notes=f"Void vendor loan #{payment.id}",
            created_by_id=user_id,
        )

    delete_ledger_by_reference("vendor_payment", payment.id, rebuild=False)
    db.session.delete(payment)
    db.session.flush()
    rebuild_party_balances("vendor", vendor_id)
    enqueue_sync("vendor_payments", payment_id, "delete")
    enqueue_sync("vendors", vendor_id, "update")
    return vendor_id


def _normalize_payment_type(raw, *, debit=None, credit=None, fallback="account_settle"):
    """Map free-text ledger type labels to advance | loan | account_settle."""
    text = (raw or "").strip().lower().replace("-", " ").replace("_", " ")
    compact = text.replace(" ", "")
    if compact == "loan" or text == "loan":
        return "loan"
    if compact == "advance" or text == "advance":
        return "advance"
    if compact in (
        "accountsettle",
        "settle",
        "settlement",
        "payment",
        "vendorpayment",
    ) or "settle" in text:
        return "account_settle"
    if raw and str(raw).strip().lower() in PAYMENT_TYPES:
        return str(raw).strip().lower()
    d = Decimal(str(debit or 0))
    c = Decimal(str(credit or 0))
    if d > 0 and c <= 0:
        return "loan"
    existing = (fallback or "account_settle").strip().lower()
    return existing if existing in PAYMENT_TYPES else "account_settle"


def sync_vendor_payment_from_ledger(entry):
    """
    Keep VendorPayment (+ cash for loans) aligned when a linked ledger row is edited.
    entry.reference_type must be vendor_payment; entry fields are already updated.
    """
    from app.services.cashbook_service import update_cash_by_reference

    ref_id = entry.reference_id
    if not ref_id:
        return None

    payment = db.session.get(VendorPayment, int(ref_id))
    if not payment:
        return None

    debit = Decimal(str(entry.debit or 0))
    credit = Decimal(str(entry.credit or 0))
    ptype = _normalize_payment_type(
        entry.entry_type,
        debit=debit,
        credit=credit,
        fallback=payment.payment_type or "account_settle",
    )

    if ptype == "loan":
        amount = debit if debit > 0 else credit
        entry.debit = amount
        entry.credit = Decimal("0")
        entry.entry_type = "loan"
    else:
        amount = credit if credit > 0 else debit
        entry.debit = Decimal("0")
        entry.credit = amount
        entry.entry_type = ptype

    if amount <= 0:
        raise ValueError("Amount must be greater than zero.")

    old_ptype = (payment.payment_type or "").strip().lower()
    payment.amount = amount
    payment.payment_date = entry.entry_date
    payment.notes = entry.notes
    payment.payment_type = ptype

    vendor = db.session.get(Vendor, payment.vendor_id)
    name = vendor.name if vendor else "vendor"
    note = entry.notes

    if ptype == "loan":
        cash_notes = f"Loan from {name}" + (f" — {note}" if note else "")
        updated = update_cash_by_reference(
            "vendor_payment",
            payment.id,
            amount=amount,
            entry_type="in",
            category="vendor_loan",
            entry_date=entry.entry_date,
            notes=cash_notes,
        )
        if updated == 0:
            record_cash_movement(
                "in",
                "vendor_loan",
                amount,
                "vendor_payment",
                payment.id,
                notes=cash_notes,
                entry_date=entry.entry_date,
            )
    elif old_ptype == "loan":
        # Payment no longer a loan — reverse any loan cash that was on the books
        reverse_cash_by_reference(
            "vendor_payment",
            payment.id,
            notes=f"Void vendor loan #{payment.id} (edited)",
        )

    enqueue_sync("vendor_payments", payment.id, "update")
    if vendor:
        enqueue_sync("vendors", vendor.id, "update")
    return payment

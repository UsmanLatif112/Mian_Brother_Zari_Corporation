from decimal import Decimal

from app.extensions import db
from app.models import AccountBalance, CashBookEntry
from app.models.mixins import utcnow


def _get_or_create_balance(account_type: str) -> AccountBalance:
    row = AccountBalance.query.filter_by(account_type=account_type).first()
    if not row:
        row = AccountBalance(account_type=account_type, balance=Decimal("0"))
        db.session.add(row)
        db.session.flush()
    return row


def get_cash_balance() -> Decimal:
    return _get_or_create_balance("cash").balance


def get_bank_balance() -> Decimal:
    return _get_or_create_balance("bank").balance


def record_cash_movement(
    entry_type: str,
    category: str,
    amount: Decimal,
    reference_type=None,
    reference_id=None,
    notes=None,
    created_by_id=None,
    entry_date=None,
):
    from datetime import date

    cash = _get_or_create_balance("cash")
    amount = Decimal(str(amount))
    if entry_type == "in":
        cash.balance += amount
    else:
        cash.balance -= amount
    cash.updated_at = utcnow()
    entry = CashBookEntry(
        entry_date=entry_date or date.today(),
        entry_type=entry_type,
        category=category,
        reference_type=reference_type,
        reference_id=reference_id,
        amount=amount,
        balance_after=cash.balance,
        notes=notes,
        created_by_id=created_by_id,
    )
    db.session.add(entry)
    return entry


def reverse_cash_by_reference(reference_type, reference_id, notes=None, created_by_id=None):
    """Undo cash movements for a reference by posting opposite entries, then remove originals."""
    rows = CashBookEntry.query.filter_by(
        reference_type=reference_type, reference_id=reference_id
    ).all()
    for row in rows:
        opposite = "out" if (row.entry_type or "").lower() == "in" else "in"
        record_cash_movement(
            opposite,
            f"void_{row.category}" if row.category else "void",
            row.amount,
            reference_type=f"void_{reference_type}",
            reference_id=reference_id,
            notes=notes or f"Void {reference_type} #{reference_id}",
            created_by_id=created_by_id,
            entry_date=row.entry_date,
        )
        db.session.delete(row)
    return len(rows)


def record_bank_movement(amount: Decimal, direction: str):
    bank = _get_or_create_balance("bank")
    amount = Decimal(str(amount))
    if direction == "in":
        bank.balance += amount
    else:
        bank.balance -= amount
    bank.updated_at = utcnow()

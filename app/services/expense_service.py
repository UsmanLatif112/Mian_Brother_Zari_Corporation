from datetime import date, datetime
from decimal import Decimal

from app.extensions import db
from app.models import Expense, ExpenseSettlement
from app.services.audit_service import log_audit
from app.services.cashbook_service import record_cash_movement, reverse_cash_by_reference


def settle_expense(expense_id, user_id, notes=None, amount=None, payment_date=None):
    expense = db.session.get(Expense, expense_id)
    if not expense or expense.is_settled:
        raise ValueError("Expense not found or already settled")

    pay_amount = Decimal(str(amount if amount is not None else expense.amount))
    if pay_amount <= 0:
        raise ValueError("Payment amount must be greater than zero.")

    entry_date = payment_date or date.today()
    if isinstance(entry_date, str):
        entry_date = date.fromisoformat(entry_date)

    note = (notes or "").strip() or None

    settlement = ExpenseSettlement(
        expense_id=expense.id,
        amount=pay_amount,
        notes=note,
        settled_by_id=user_id,
        settled_at=datetime.combine(entry_date, datetime.min.time()),
    )
    expense.is_settled = True
    db.session.add(settlement)
    record_cash_movement(
        "out",
        "expense_settlement",
        pay_amount,
        "expense",
        expense.id,
        notes=note or f"Settled: {expense.name}",
        created_by_id=user_id,
        entry_date=entry_date,
    )
    log_audit("settle", "expense", expense.id, note or f"paid {pay_amount}")
    return settlement


def update_expense(expense_id, name=None, description=None, amount=None, category_id=None, expense_date=None):
    expense = db.session.get(Expense, expense_id)
    if not expense or expense.is_deleted:
        raise ValueError("Expense not found.")
    if expense.is_settled:
        raise ValueError("Cannot edit a settled expense. Delete it first or leave settled.")
    if name is not None:
        name = (name or "").strip()
        if not name:
            raise ValueError("Expense name is required.")
        expense.name = name
    if description is not None:
        expense.description = (description or "").strip() or None
    if amount is not None:
        amt = Decimal(str(amount))
        if amt <= 0:
            raise ValueError("Amount must be greater than zero.")
        expense.amount = amt
    if category_id is not None and category_id != "":
        expense.category_id = int(category_id)
    if expense_date is not None:
        if isinstance(expense_date, str):
            expense.expense_date = date.fromisoformat(expense_date)
        else:
            expense.expense_date = expense_date
    log_audit("update", "expense", expense.id, expense.name)
    return expense


def delete_expense(expense_id, user_id=None):
    expense = db.session.get(Expense, expense_id)
    if not expense or expense.is_deleted:
        raise ValueError("Expense not found.")
    if expense.is_settled:
        reverse_cash_by_reference(
            "expense",
            expense.id,
            notes=f"Void expense settlement: {expense.name}",
            created_by_id=user_id,
        )
        for s in list(expense.settlements):
            db.session.delete(s)
        expense.is_settled = False
    expense.is_deleted = True
    from app.models.mixins import utcnow

    expense.deleted_at = utcnow()
    log_audit("delete", "expense", expense.id, expense.name)
    return expense

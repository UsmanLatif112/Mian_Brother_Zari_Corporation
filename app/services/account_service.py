"""Account page — amount taken & previous-balance chain."""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models import AccountAmountTaken, AccountCashSetup
from app.services.cashbook_service import record_cash_movement, reverse_cash_by_reference


def get_latest_setup() -> AccountCashSetup | None:
    return (
        AccountCashSetup.query.order_by(
            AccountCashSetup.balance_date.desc(),
            AccountCashSetup.id.desc(),
        ).first()
    )


def opening_balance_for(as_of: date) -> Decimal:
    setup = (
        AccountCashSetup.query.filter(AccountCashSetup.balance_date <= as_of)
        .order_by(AccountCashSetup.balance_date.desc(), AccountCashSetup.id.desc())
        .first()
    )
    if setup:
        return Decimal(str(setup.previous_balance or 0))
    return Decimal("0")


def recalculate_balances() -> None:
    """Rebuild previous_balance / balance_after for all non-deleted rows."""
    setup = get_latest_setup()
    start_date = setup.balance_date if setup else date.min
    running = Decimal(str(setup.previous_balance or 0)) if setup else Decimal("0")

    rows = (
        AccountAmountTaken.query.filter_by(is_deleted=False)
        .order_by(AccountAmountTaken.taken_date.asc(), AccountAmountTaken.id.asc())
        .all()
    )
    for row in rows:
        if setup and row.taken_date < start_date:
            # Entries before setup date: keep chain from zero-ish running
            row.previous_balance = running
        else:
            # First entry on/after setup uses setup balance (already in running)
            row.previous_balance = running
        amt = Decimal(str(row.amount or 0))
        row.balance_after = Decimal(str(row.previous_balance)) - amt
        running = Decimal(str(row.balance_after))


def current_previous_balance() -> Decimal:
    """Balance before the next take (last balance_after, or setup, or 0)."""
    return account_previous_amount()


def account_previous_amount() -> Decimal:
    """Account previous balance for cash KPIs (setup / last take chain; never cash till)."""
    last = (
        AccountAmountTaken.query.filter_by(is_deleted=False)
        .order_by(AccountAmountTaken.taken_date.desc(), AccountAmountTaken.id.desc())
        .first()
    )
    if last:
        return Decimal(str(last.balance_after or 0))
    setup = get_latest_setup()
    if setup:
        return Decimal(str(setup.previous_balance or 0))
    return Decimal("0")


def computed_cash_in_hand(period="all", start_date=None, end_date=None) -> dict:
    """
    Same formula as dashboard (collections-based):
    - previous_balance from Account table chain
    - cash_without_prev_and_expense = General Journal Total In
    - cash_without_expense = journal In + previous
    - cash_in_hand = journal In + previous - expense
    """
    from app.models import Expense, Sale
    from app.services.cashbook_service import get_cash_dashboard_metrics, period_cash_collections
    from app.services.dashboard_service import _range_for_filter, _sum_period

    start, end = _range_for_filter(period, start_date, end_date)
    total_sale = _sum_period(Sale.grand_total, Sale.sale_date, start, end)
    expense_q = Expense.query.filter(Expense.is_deleted.is_(False))
    # Use ORM sum via dashboard helper path
    from sqlalchemy import func
    from app.extensions import db

    exp_q = db.session.query(func.coalesce(func.sum(Expense.amount), 0)).filter(
        Expense.is_deleted.is_(False)
    )
    if start and end:
        exp_q = exp_q.filter(Expense.expense_date >= start, Expense.expense_date <= end)
    total_expense = exp_q.scalar() or Decimal("0")
    previous = account_previous_amount()
    collections = period_cash_collections(start, end)
    metrics = get_cash_dashboard_metrics(
        total_sale=collections,
        previous_amount=previous,
        total_expense=total_expense,
    )
    metrics["previous_balance"] = previous
    metrics["total_sale"] = total_sale
    metrics["cash_collections"] = collections
    metrics["total_expense"] = total_expense
    return metrics


def save_setup(balance_date: date, previous_balance: Decimal, user_id=None, notes=None) -> AccountCashSetup:
    previous_balance = Decimal(str(previous_balance))
    row = AccountCashSetup(
        balance_date=balance_date,
        previous_balance=previous_balance,
        notes=(notes or "").strip() or None,
        created_by_id=user_id,
    )
    db.session.add(row)
    db.session.flush()
    recalculate_balances()
    return row


def create_amount_taken(
    taken_date: date,
    taken_by: str,
    amount: Decimal,
    user_id=None,
    notes=None,
) -> AccountAmountTaken:
    taken_by = (taken_by or "").strip()
    amount = Decimal(str(amount))
    if not taken_by:
        raise ValueError("Name (taken by) is required.")
    if amount <= 0:
        raise ValueError("Amount must be greater than zero.")

    # Snapshot previous from Account chain before this take (logged on the row)
    prev = account_previous_amount()
    row = AccountAmountTaken(
        taken_date=taken_date,
        taken_by=taken_by,
        amount=amount,
        previous_balance=prev,
        balance_after=prev - amount,
        notes=(notes or "").strip() or None,
        created_by_id=user_id,
    )
    db.session.add(row)
    db.session.flush()

    record_cash_movement(
        "out",
        "amount_taken",
        amount,
        reference_type="amount_taken",
        reference_id=row.id,
        notes=f"Amount taken by {taken_by} (prev {prev})",
        created_by_id=user_id,
        entry_date=taken_date,
    )
    recalculate_balances()
    return row


def update_amount_taken(
    row_id: int,
    taken_date: date,
    taken_by: str,
    amount: Decimal,
    user_id=None,
    notes=None,
) -> AccountAmountTaken:
    row = db.session.get(AccountAmountTaken, row_id)
    if not row or row.is_deleted:
        raise ValueError("Record not found.")

    taken_by = (taken_by or "").strip()
    amount = Decimal(str(amount))
    if not taken_by:
        raise ValueError("Name (taken by) is required.")
    if amount <= 0:
        raise ValueError("Amount must be greater than zero.")

    reverse_cash_by_reference(
        "amount_taken",
        row.id,
        notes=f"Edit amount taken #{row.id}",
        created_by_id=user_id,
    )

    row.taken_date = taken_date
    row.taken_by = taken_by
    row.amount = amount
    row.notes = (notes or "").strip() or None

    record_cash_movement(
        "out",
        "amount_taken",
        amount,
        reference_type="amount_taken",
        reference_id=row.id,
        notes=f"Amount taken by {taken_by}",
        created_by_id=user_id,
        entry_date=taken_date,
    )
    recalculate_balances()
    return row


def delete_amount_taken(row_id: int, user_id=None) -> None:
    row = db.session.get(AccountAmountTaken, row_id)
    if not row or row.is_deleted:
        raise ValueError("Record not found.")
    reverse_cash_by_reference(
        "amount_taken",
        row.id,
        notes=f"Delete amount taken #{row.id}",
        created_by_id=user_id,
    )
    row.soft_delete()
    recalculate_balances()

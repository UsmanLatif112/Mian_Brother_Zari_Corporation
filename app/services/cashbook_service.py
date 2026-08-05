from decimal import Decimal

from sqlalchemy import func

from app.extensions import db
from app.models import AccountBalance, CashBookEntry
from app.models.mixins import utcnow

# Paid-to-vendor cash outs — excluded from all cash-in-hand displays
VENDOR_PAY_CATEGORIES = (
    "vendor_settle",
    "vendor_advance",
    "void_vendor_settle",
    "void_vendor_advance",
)
EXPENSE_CATEGORIES = (
    "expense_spent",
    "expense_settlement",
    "void_expense_spent",
    "void_expense_settlement",
)
AMOUNT_TAKEN_CATEGORIES = (
    "amount_taken",
    "void_amount_taken",
)


def _get_or_create_balance(account_type: str) -> AccountBalance:
    row = AccountBalance.query.filter_by(account_type=account_type).first()
    if not row:
        row = AccountBalance(account_type=account_type, balance=Decimal("0"))
        db.session.add(row)
        db.session.flush()
    return row


def get_cash_balance() -> Decimal:
    """Raw till balance (includes all recorded movements)."""
    return Decimal(str(_get_or_create_balance("cash").balance or 0))


def get_bank_balance() -> Decimal:
    return _get_or_create_balance("bank").balance


def net_cash_effect(categories) -> Decimal:
    """
    Net effect of categories on cash: sum(in) - sum(out).
    Positive means those entries increased cash overall.
    """
    cats = list(categories)
    if not cats:
        return Decimal("0")
    inflow = (
        db.session.query(func.coalesce(func.sum(CashBookEntry.amount), 0))
        .filter(
            CashBookEntry.entry_type == "in",
            CashBookEntry.category.in_(cats),
        )
        .scalar()
    ) or Decimal("0")
    outflow = (
        db.session.query(func.coalesce(func.sum(CashBookEntry.amount), 0))
        .filter(
            CashBookEntry.entry_type == "out",
            CashBookEntry.category.in_(cats),
        )
        .scalar()
    ) or Decimal("0")
    return Decimal(str(inflow)) - Decimal(str(outflow))


def get_cash_balance_excluding(categories) -> Decimal:
    """Cash as if listed category movements never happened."""
    return get_cash_balance() - net_cash_effect(categories)


def get_cash_in_hand() -> Decimal:
    """Shop cash in hand — excludes amounts paid to vendors (settle / advance)."""
    return get_cash_balance_excluding(VENDOR_PAY_CATEGORIES)


def get_cash_dashboard_metrics(total_sale=None, previous_amount=None, total_expense=None) -> dict:
    """
    Dashboard cash cards (collections-based, not credit sales):
    - Cash (w/o Prev. Bal. & Expense) = cash actually received
    - Previous Balance = Account previous amount
    - Cash (w/o Expense) = collections + Previous Amount
    - Cash In Hand = collections + Previous Amount - Expense
    """
    sale = Decimal(str(total_sale if total_sale is not None else 0))
    prev = Decimal(str(previous_amount if previous_amount is not None else 0))
    expense = Decimal(str(total_expense if total_expense is not None else 0))
    without_expense = sale + prev
    return {
        "cash_without_prev_and_expense": sale,
        "previous_balance": prev,
        "cash_without_expense": without_expense,
        "cash_in_hand": without_expense - expense,
    }


def period_cash_collections(start=None, end=None) -> Decimal:
    """
    Cash actually received in the period:
    - Sale amount paid (cash at sale time)
    - Customer advance / account settle collections
    - Vendor loan cash in
    Minus customer loans (cash out to customer).
    Credit/unpaid sale totals are excluded.
    """
    from app.models import CustomerReceiving, Sale, VendorPayment

    cash = Decimal("0")

    sale_q = db.session.query(func.coalesce(func.sum(Sale.amount_paid), 0))
    if start and end:
        sale_q = sale_q.filter(Sale.sale_date >= start, Sale.sale_date <= end)
    cash += Decimal(str(sale_q.scalar() or 0))

    cust_in_q = db.session.query(func.coalesce(func.sum(CustomerReceiving.amount), 0)).filter(
        CustomerReceiving.payment_type.in_(("advance", "account_settle"))
    )
    if start and end:
        cust_in_q = cust_in_q.filter(
            CustomerReceiving.receiving_date >= start,
            CustomerReceiving.receiving_date <= end,
        )
    cash += Decimal(str(cust_in_q.scalar() or 0))

    cust_loan_q = db.session.query(func.coalesce(func.sum(CustomerReceiving.amount), 0)).filter(
        CustomerReceiving.payment_type == "loan"
    )
    if start and end:
        cust_loan_q = cust_loan_q.filter(
            CustomerReceiving.receiving_date >= start,
            CustomerReceiving.receiving_date <= end,
        )
    cash -= Decimal(str(cust_loan_q.scalar() or 0))

    vendor_loan_q = db.session.query(func.coalesce(func.sum(VendorPayment.amount), 0)).filter(
        VendorPayment.payment_type == "loan"
    )
    if start and end:
        vendor_loan_q = vendor_loan_q.filter(
            VendorPayment.payment_date >= start,
            VendorPayment.payment_date <= end,
        )
    cash += Decimal(str(vendor_loan_q.scalar() or 0))

    return cash


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
    from app.utils.working_date import get_working_date

    cash = _get_or_create_balance("cash")
    amount = Decimal(str(amount))
    if entry_type == "in":
        cash.balance += amount
    else:
        cash.balance -= amount
    cash.updated_at = utcnow()
    entry = CashBookEntry(
        entry_date=entry_date or get_working_date(),
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


def update_cash_by_reference(
    reference_type,
    reference_id,
    *,
    amount,
    entry_type,
    category=None,
    entry_date=None,
    notes=None,
):
    """
    Update existing non-void cash rows for a source document and adjust till balance.
    Reverses old effect(s), then applies the new amount/direction.
    """
    rows = CashBookEntry.query.filter_by(
        reference_type=reference_type, reference_id=reference_id
    ).all()
    # Ignore void_* companion rows if any remain linked to the same reference_id
    live = [r for r in rows if not (r.category or "").startswith("void_")]
    if not live:
        return 0

    cash = _get_or_create_balance("cash")
    new_amount = Decimal(str(amount or 0))
    new_type = (entry_type or "").strip().lower()
    if new_type not in ("in", "out"):
        raise ValueError("Cash entry type must be 'in' or 'out'.")

    for row in live:
        old_amount = Decimal(str(row.amount or 0))
        old_type = (row.entry_type or "").strip().lower()
        if old_type == "in":
            cash.balance -= old_amount
        else:
            cash.balance += old_amount

        if new_type == "in":
            cash.balance += new_amount
        else:
            cash.balance -= new_amount

        row.amount = new_amount
        row.entry_type = new_type
        if category is not None:
            row.category = category
        if entry_date is not None:
            row.entry_date = entry_date
        if notes is not None:
            row.notes = notes
        row.balance_after = cash.balance

    cash.updated_at = utcnow()
    return len(live)


def record_bank_movement(amount: Decimal, direction: str):
    bank = _get_or_create_balance("bank")
    amount = Decimal(str(amount))
    if direction == "in":
        bank.balance += amount
    else:
        bank.balance -= amount
    bank.updated_at = utcnow()

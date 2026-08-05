"""Account page models — amount taken / previous balance tracking."""

from decimal import Decimal

from app.extensions import db
from app.models.mixins import AgencyMixin, SoftDeleteMixin, TimestampMixin, utcnow
from app.utils.working_date import default_entry_date


class AccountCashSetup(AgencyMixin, db.Model):
    """Opening / previous balance effective from a given date."""

    __tablename__ = "account_cash_setups"

    id = db.Column(db.Integer, primary_key=True)
    balance_date = db.Column(db.Date, nullable=False, index=True)
    previous_balance = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0"))
    notes = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    created_by = db.relationship("User")


class AccountAmountTaken(AgencyMixin, SoftDeleteMixin, TimestampMixin, db.Model):
    """Cash taken from the till by a person."""

    __tablename__ = "account_amount_taken"

    id = db.Column(db.Integer, primary_key=True)
    taken_date = db.Column(db.Date, nullable=False, index=True, default=default_entry_date)
    taken_by = db.Column(db.String(120), nullable=False)
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    previous_balance = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0"))
    balance_after = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0"))
    notes = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    created_by = db.relationship("User")

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.mixins import SoftDeleteMixin, TimestampMixin

# good | bad | 1_year | 6_month | late_pay
CUSTOMER_TYPE_CHOICES = [
    ("good", "Good"),
    ("bad", "Bad"),
    ("1_year", "1 Year"),
    ("6_month", "6 Month"),
    ("late_pay", "Late Pay"),
]


class Customer(SoftDeleteMixin, TimestampMixin, db.Model):
    __tablename__ = "customers"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False, index=True)
    phone = db.Column(db.String(30), nullable=True, index=True)
    cnic = db.Column(db.String(20), nullable=True)
    address = db.Column(db.Text, nullable=True)
    customer_type = db.Column(db.String(30), default="good", nullable=False, index=True)
    old_book_no = db.Column(db.String(50), nullable=True, index=True)
    joined_date = db.Column(db.Date, default=date.today, nullable=True, index=True)
    opening_balance = db.Column(db.Numeric(14, 2), default=Decimal("0"))
    credit_limit = db.Column(db.Numeric(14, 2), default=Decimal("0"))
    notes = db.Column(db.Text, nullable=True)
    balance = db.Column(db.Numeric(14, 2), default=Decimal("0"))
    remote_id = db.Column(db.Integer, nullable=True, index=True)
    photo = db.Column(db.String(255), nullable=True)

    @property
    def customer_type_label(self):
        return dict(CUSTOMER_TYPE_CHOICES).get(self.customer_type, self.customer_type)

    @property
    def photo_url(self):
        if not self.photo:
            return None
        return f"/static/uploads/{self.photo}"


class Vendor(SoftDeleteMixin, TimestampMixin, db.Model):
    __tablename__ = "vendors"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False, index=True)
    phone = db.Column(db.String(30), nullable=True)
    address = db.Column(db.Text, nullable=True)
    opening_balance = db.Column(db.Numeric(14, 2), default=Decimal("0"))
    notes = db.Column(db.Text, nullable=True)
    balance = db.Column(db.Numeric(14, 2), default=Decimal("0"))
    remote_id = db.Column(db.Integer, nullable=True, index=True)
    photo = db.Column(db.String(255), nullable=True)

    @property
    def photo_url(self):
        if not self.photo:
            return None
        return f"/static/uploads/{self.photo}"


class LedgerEntry(db.Model):
    __tablename__ = "ledger_entries"

    id = db.Column(db.Integer, primary_key=True)
    party_type = db.Column(db.String(20), nullable=False, index=True)  # customer / vendor
    party_id = db.Column(db.Integer, nullable=False, index=True)
    entry_date = db.Column(db.Date, default=date.today, nullable=False, index=True)
    entry_type = db.Column(db.String(30), nullable=False)
    reference_type = db.Column(db.String(30), nullable=True)
    reference_id = db.Column(db.Integer, nullable=True)
    debit = db.Column(db.Numeric(14, 2), default=Decimal("0"))
    credit = db.Column(db.Numeric(14, 2), default=Decimal("0"))
    balance_after = db.Column(db.Numeric(14, 2), nullable=False)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False)

    __table_args__ = (
        db.Index("ix_ledger_party", "party_type", "party_id"),
    )

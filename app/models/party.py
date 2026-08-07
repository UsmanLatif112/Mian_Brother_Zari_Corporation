from decimal import Decimal

from app.extensions import db
from app.models.mixins import SoftDeleteMixin, TimestampMixin
from app.utils.working_date import default_entry_date

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
    joined_date = db.Column(db.Date, default=default_entry_date, nullable=True, index=True)
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


class Salesman(SoftDeleteMixin, TimestampMixin, db.Model):
    """Field officer / referral person (not a login user). Optional on each sale."""

    __tablename__ = "salesmen"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False, index=True)
    phone = db.Column(db.String(30), nullable=True, index=True)
    company = db.Column(db.String(200), nullable=True)
    address = db.Column(db.Text, nullable=True)
    opening_balance = db.Column(db.Numeric(14, 2), default=Decimal("0"))
    notes = db.Column(db.Text, nullable=True)
    # Outstanding credit attributable to this salesman (debit − credit on ledger)
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
    party_type = db.Column(db.String(20), nullable=False, index=True)  # customer / vendor / salesman
    party_id = db.Column(db.Integer, nullable=False, index=True)
    entry_date = db.Column(db.Date, default=default_entry_date, nullable=False, index=True)
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

    @property
    def status_label(self):
        """Human status for customer/vendor ledger rows."""
        et = (self.entry_type or "").strip().lower()
        debit = Decimal(str(self.debit or 0))
        credit = Decimal(str(self.credit or 0))

        if et in ("advance", "sale_advance"):
            return "Advance"
        if et == "loan":
            return "Loan"
        if et == "account_settle":
            return "Settle"
        if et == "opening":
            return "Opening"
        if et == "sale":
            if debit <= 0 and credit <= 0:
                return "Sale"
            if credit <= 0:
                return "Unpaid"
            if credit + Decimal("0.001") >= debit:
                return "Paid"
            return "Partial"
        if et == "purchase":
            if credit <= 0 and debit > 0:
                return "Unpaid"
            if debit <= 0 and credit > 0:
                return "Paid"
            return "Purchase"
        # Fallback: title-case entry type
        return (self.entry_type or "—").replace("_", " ").title()

    @property
    def status_badge_class(self):
        label = self.status_label
        return {
            "Paid": "bg-success-subtle text-success border border-success-subtle",
            "Unpaid": "bg-danger-subtle text-danger border border-danger-subtle",
            "Partial": "bg-warning-subtle text-warning border border-warning-subtle",
            "Advance": "bg-info-subtle text-info border border-info-subtle",
            "Loan": "bg-primary-subtle text-primary border border-primary-subtle",
            "Settle": "bg-success-subtle text-success border border-success-subtle",
            "Opening": "bg-secondary-subtle text-secondary border border-secondary-subtle",
        }.get(label, "bg-light text-dark border")

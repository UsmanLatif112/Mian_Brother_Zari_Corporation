import enum
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.mixins import TimestampMixin, utcnow


class PaymentMethod(str, enum.Enum):
    CASH = "cash"
    BANK = "bank"
    CREDIT = "credit"


class PaymentStatus(str, enum.Enum):
    PAID = "paid"
    PARTIAL = "partial"
    UNPAID = "unpaid"


class Sale(TimestampMixin, db.Model):
    __tablename__ = "sales"

    id = db.Column(db.Integer, primary_key=True)
    invoice_no = db.Column(db.String(50), unique=True, nullable=False, index=True)
    sale_date = db.Column(db.Date, default=date.today, nullable=False, index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=True)
    subtotal = db.Column(db.Numeric(14, 2), default=Decimal("0"))
    discount = db.Column(db.Numeric(14, 2), default=Decimal("0"))
    tax_amount = db.Column(db.Numeric(14, 2), default=Decimal("0"))
    grand_total = db.Column(db.Numeric(14, 2), default=Decimal("0"))
    payment_method = db.Column(db.Enum(PaymentMethod), default=PaymentMethod.CASH)
    payment_status = db.Column(db.Enum(PaymentStatus), default=PaymentStatus.PAID)
    amount_paid = db.Column(db.Numeric(14, 2), default=Decimal("0"))
    notes = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    remote_id = db.Column(db.Integer, nullable=True, index=True)

    customer = db.relationship("Customer", backref="sales")
    created_by = db.relationship("User")
    items = db.relationship("SaleItem", backref="sale", cascade="all, delete-orphan")


class SaleItem(db.Model):
    __tablename__ = "sale_items"

    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    quantity = db.Column(db.Numeric(14, 3), nullable=False)
    unit_price = db.Column(db.Numeric(14, 2), nullable=False)
    discount = db.Column(db.Numeric(14, 2), default=Decimal("0"))
    tax_rate = db.Column(db.Numeric(5, 2), default=Decimal("0"))
    line_total = db.Column(db.Numeric(14, 2), nullable=False)
    cost_of_goods = db.Column(db.Numeric(14, 2), default=Decimal("0"))

    product = db.relationship("Product")


class CustomerReceiving(db.Model):
    __tablename__ = "customer_receivings"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False)
    receiving_date = db.Column(db.Date, default=date.today, nullable=False, index=True)
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    # advance | loan | account_settle
    payment_type = db.Column(db.String(30), default="account_settle", nullable=False, index=True)
    payment_method = db.Column(db.Enum(PaymentMethod), default=PaymentMethod.CASH)
    notes = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    customer = db.relationship("Customer")
    created_by = db.relationship("User")

    @property
    def payment_type_label(self):
        return {
            "advance": "Advance",
            "loan": "Loan",
            "account_settle": "Account Settle",
        }.get(self.payment_type or "account_settle", self.payment_type)

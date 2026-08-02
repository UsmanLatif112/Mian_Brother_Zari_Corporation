from decimal import Decimal

from app.extensions import db
from app.models.mixins import TimestampMixin
from app.utils.working_date import default_entry_date
from app.models.sales import PaymentMethod, PaymentStatus


class Purchase(TimestampMixin, db.Model):
    __tablename__ = "purchases"

    id = db.Column(db.Integer, primary_key=True)
    invoice_no = db.Column(db.String(50), nullable=False, index=True)
    vendor_id = db.Column(db.Integer, db.ForeignKey("vendors.id"), nullable=False)
    purchase_date = db.Column(db.Date, default=default_entry_date, nullable=False, index=True)
    subtotal = db.Column(db.Numeric(14, 2), default=Decimal("0"))
    discount = db.Column(db.Numeric(14, 2), default=Decimal("0"))
    tax_amount = db.Column(db.Numeric(14, 2), default=Decimal("0"))
    transport_charges = db.Column(db.Numeric(14, 2), default=Decimal("0"))
    grand_total = db.Column(db.Numeric(14, 2), default=Decimal("0"))
    payment_status = db.Column(db.Enum(PaymentStatus), default=PaymentStatus.UNPAID)
    amount_paid = db.Column(db.Numeric(14, 2), default=Decimal("0"))
    notes = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    remote_id = db.Column(db.Integer, nullable=True, index=True)

    vendor = db.relationship("Vendor", backref="purchases")
    created_by = db.relationship("User")
    items = db.relationship(
        "PurchaseItem", backref="purchase", cascade="all, delete-orphan"
    )


class PurchaseItem(db.Model):
    __tablename__ = "purchase_items"

    id = db.Column(db.Integer, primary_key=True)
    purchase_id = db.Column(db.Integer, db.ForeignKey("purchases.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    quantity = db.Column(db.Numeric(14, 3), nullable=False)
    unit_price = db.Column(db.Numeric(14, 2), nullable=False)
    discount = db.Column(db.Numeric(14, 2), default=Decimal("0"))
    tax_rate = db.Column(db.Numeric(5, 2), default=Decimal("0"))
    line_total = db.Column(db.Numeric(14, 2), nullable=False)

    product = db.relationship("Product")


class VendorPayment(db.Model):
    __tablename__ = "vendor_payments"

    id = db.Column(db.Integer, primary_key=True)
    vendor_id = db.Column(db.Integer, db.ForeignKey("vendors.id"), nullable=False)
    payment_date = db.Column(db.Date, default=default_entry_date, nullable=False, index=True)
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    # advance | loan | account_settle
    payment_type = db.Column(db.String(30), default="account_settle", nullable=False, index=True)
    payment_method = db.Column(db.Enum(PaymentMethod), default=PaymentMethod.CASH)
    notes = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, nullable=False)

    vendor = db.relationship("Vendor")
    created_by = db.relationship("User")

    @property
    def payment_type_label(self):
        return {
            "advance": "Advance",
            "loan": "Loan",
            "account_settle": "Account Settle",
        }.get(self.payment_type or "account_settle", self.payment_type)

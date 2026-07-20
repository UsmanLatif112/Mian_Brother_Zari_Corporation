from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.mixins import SoftDeleteMixin, TimestampMixin, utcnow


class ExpenseCategory(SoftDeleteMixin, db.Model):
    __tablename__ = "expense_categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)


class Expense(SoftDeleteMixin, TimestampMixin, db.Model):
    __tablename__ = "expenses"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("expense_categories.id"))
    expense_date = db.Column(db.Date, default=date.today, nullable=False, index=True)
    is_settled = db.Column(db.Boolean, default=False, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    remote_id = db.Column(db.Integer, nullable=True, index=True)

    category = db.relationship("ExpenseCategory")
    created_by = db.relationship("User")
    settlements = db.relationship(
        "ExpenseSettlement", backref="expense", cascade="all, delete-orphan"
    )


class ExpenseSettlement(db.Model):
    __tablename__ = "expense_settlements"

    id = db.Column(db.Integer, primary_key=True)
    expense_id = db.Column(db.Integer, db.ForeignKey("expenses.id"), nullable=False)
    settled_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    notes = db.Column(db.Text, nullable=True)
    settled_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    settled_by = db.relationship("User")

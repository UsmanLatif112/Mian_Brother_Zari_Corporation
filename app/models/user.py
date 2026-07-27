import enum
from datetime import date
from decimal import Decimal

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models.mixins import SoftDeleteMixin, TimestampMixin, utcnow


class UserRole(str, enum.Enum):
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    MANAGER = "manager"
    SALES = "sales"
    ACCOUNTANT = "accountant"


PRIVILEGED_ROLES = frozenset({UserRole.SUPER_ADMIN})

ROLE_PERMISSIONS = {
    UserRole.SUPER_ADMIN: {"*"},
    UserRole.ADMIN: {"*"},
    UserRole.MANAGER: {
        "dashboard.view",
        "inventory.*",
        "sales.*",
        "purchases.*",
        "expenses.*",
        "customers.*",
        "vendors.*",
        "reports.view",
        "reports.*",
        "cashbook.*",
        "settings.view",
        "backup.view",
        "sync.view",
    },
    UserRole.SALES: {
        "dashboard.view",
        "inventory.view",
        "sales.*",
        "customers.view",
        "customers.create",
        "reports.sales",
    },
    UserRole.ACCOUNTANT: {
        "dashboard.view",
        "expenses.*",
        "purchases.view",
        "customers.view",
        "customers.create",
        "vendors.view",
        "reports.*",
        "cashbook.*",
        "backup.view",
    },
}


class User(UserMixin, TimestampMixin, SoftDeleteMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    role = db.Column(
        db.Enum(UserRole, values_callable=lambda x: [e.value for e in x]),
        default=UserRole.SALES,
        nullable=False,
    )
    is_active_user = db.Column(db.Boolean, default=True, nullable=False)
    last_login_at = db.Column(db.DateTime, nullable=True)
    remote_id = db.Column(db.Integer, nullable=True, index=True)
    is_registered = db.Column(db.Boolean, default=False, nullable=False)
    registration_key = db.Column(db.String(32), nullable=True)
    registered_at = db.Column(db.DateTime, nullable=True)
    device_id = db.Column(db.String(64), nullable=True)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def has_permission(self, permission: str) -> bool:
        perms = ROLE_PERMISSIONS.get(self.role, set())
        if "*" in perms:
            return True
        if permission in perms:
            return True
        prefix = permission.split(".")[0]
        return f"{prefix}.*" in perms

    def is_super_admin(self) -> bool:
        return self.role == UserRole.SUPER_ADMIN

    @property
    def role_label(self) -> str:
        if self.role == UserRole.SUPER_ADMIN:
            return "Super Admin"
        return self.role.value.replace("_", " ").title()

    @property
    def is_active(self):
        return self.is_active_user and not self.is_deleted

    def is_registration_complete(self) -> bool:
        if self.role in PRIVILEGED_ROLES:
            return True
        return bool(self.is_registered)

    @property
    def registration_status_label(self) -> str:
        if self.role in PRIVILEGED_ROLES:
            return "Registered"
        if self.is_registered:
            return "Registered"
        return "Not Registered"


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    action = db.Column(db.String(50), nullable=False, index=True)
    entity_type = db.Column(db.String(80), nullable=False, index=True)
    entity_id = db.Column(db.String(80), nullable=True)
    details = db.Column(db.Text, nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)

    user = db.relationship("User", backref="audit_logs")


class Setting(db.Model):
    __tablename__ = "settings"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False, index=True)
    value = db.Column(db.Text, nullable=True)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)


class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(50), default="info")
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    user = db.relationship("User", backref="notifications")


class AccountBalance(db.Model):
    """Singleton-style rows for cash and bank."""

    __tablename__ = "account_balances"

    id = db.Column(db.Integer, primary_key=True)
    account_type = db.Column(db.String(20), unique=True, nullable=False)
    balance = db.Column(db.Numeric(14, 2), default=Decimal("0"), nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)


class CashBookEntry(db.Model):
    __tablename__ = "cash_book_entries"

    id = db.Column(db.Integer, primary_key=True)
    entry_date = db.Column(db.Date, default=date.today, nullable=False, index=True)
    entry_type = db.Column(db.String(20), nullable=False)  # in / out
    category = db.Column(db.String(50), nullable=False)
    reference_type = db.Column(db.String(50), nullable=True)
    reference_id = db.Column(db.Integer, nullable=True)
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    balance_after = db.Column(db.Numeric(14, 2), nullable=False)
    notes = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    created_by = db.relationship("User")

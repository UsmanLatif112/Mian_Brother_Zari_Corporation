import enum
from datetime import date, datetime, timedelta
from decimal import Decimal

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models.mixins import AgencyMixin, SoftDeleteMixin, TimestampMixin, utcnow
from app.utils.working_date import default_entry_date

# License types stored in users.license_type / erp_user_registry.license_type
LICENSE_TRIAL = "trial"
LICENSE_LIFETIME = "lifetime"
LICENSE_MONTHLY = "monthly"  # reserved for future paid subscriptions

TRIAL_DAYS = 7


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


def default_trial_expires_at(from_dt: datetime | None = None) -> datetime:
    start = from_dt or utcnow()
    return start + timedelta(days=TRIAL_DAYS)


class User(UserMixin, AgencyMixin, TimestampMixin, SoftDeleteMixin, db.Model):
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
    # Shop ownership:
    # - Admin: agency_id == id (this user IS the agency / shop)
    # - Staff: agency_id points to their shop Admin user id
    # AgencyMixin already defines agency_id; documented here for User semantics.
    is_registered = db.Column(db.Boolean, default=False, nullable=False)
    registration_key = db.Column(db.String(32), nullable=True)
    registered_at = db.Column(db.DateTime, nullable=True)
    device_id = db.Column(db.String(64), nullable=True)
    # trial | lifetime | monthly (monthly reserved for future releases)
    license_type = db.Column(db.String(20), nullable=True)
    # Trial / monthly end date; null for lifetime
    license_expires_at = db.Column(db.DateTime, nullable=True)
    # Per-tenant branding (set by Super Admin when creating the user)
    company_name = db.Column(db.String(200), nullable=True)
    company_logo = db.Column(db.String(255), nullable=True)
    # Raw image bytes so logo survives in main DB + staff package DB even if file is missing
    company_logo_data = db.Column(db.LargeBinary, nullable=True)

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
    def company_logo_url(self) -> str | None:
        """Public URL for this user's company logo (file restored from DB blob if needed)."""
        from app.utils.company_branding import materialize_logo_from_blob
        from app.utils.uploads import image_url

        path = materialize_logo_from_blob(self)
        if not path:
            return None
        return image_url(path)

    @property
    def role_label(self) -> str:
        if self.role == UserRole.SUPER_ADMIN:
            return "Super Admin"
        return self.role.value.replace("_", " ").title()

    @property
    def is_active(self):
        return self.is_active_user and not self.is_deleted

    def _license_expires_naive(self) -> datetime | None:
        expires = self.license_expires_at
        if expires is None:
            return None
        if getattr(expires, "tzinfo", None) is not None:
            return expires.replace(tzinfo=None)
        return expires

    def is_on_trial(self) -> bool:
        """Active trial — full app access without PC key yet."""
        if self.role in PRIVILEGED_ROLES or self.is_registered:
            return False
        if (self.license_type or "") != LICENSE_TRIAL:
            return False
        expires = self._license_expires_naive()
        if not expires:
            return False
        return expires > utcnow()

    def is_trial_expired(self) -> bool:
        if self.role in PRIVILEGED_ROLES or self.is_registered:
            return False
        if (self.license_type or "") != LICENSE_TRIAL:
            return False
        expires = self._license_expires_naive()
        return bool(expires and expires <= utcnow())

    def trial_days_remaining(self) -> int | None:
        if not self.is_on_trial():
            return None
        expires = self._license_expires_naive()
        if not expires:
            return None
        delta = expires - utcnow()
        days = int(delta.total_seconds() // 86400)
        return max(0, days)

    def start_trial(self, amount: int = TRIAL_DAYS, unit: str = "days") -> None:
        """Start / restart trial. unit: minutes|hours|days|weeks|months."""
        self.license_type = LICENSE_TRIAL
        try:
            amount = max(1, int(amount or TRIAL_DAYS))
        except (TypeError, ValueError):
            amount = TRIAL_DAYS
        unit = (unit or "days").strip().lower()
        if unit in ("minute", "minutes", "min", "m"):
            delta = timedelta(minutes=amount)
        elif unit in ("hour", "hours", "hr", "h"):
            delta = timedelta(hours=amount)
        elif unit in ("week", "weeks", "w"):
            delta = timedelta(weeks=amount)
        elif unit in ("month", "months", "mo"):
            delta = timedelta(days=amount * 30)
        else:
            delta = timedelta(days=amount)
        self.license_expires_at = utcnow() + delta
        self.is_registered = False
        self.registered_at = None
        self.device_id = None

    def activate_lifetime(self) -> None:
        """After registration key — lifetime license."""
        self.is_registered = True
        self.license_type = LICENSE_LIFETIME
        self.license_expires_at = None

    def trial_remaining_parts(self) -> tuple[int, int, int, int] | None:
        """(days, hours, minutes, seconds) remaining, or None if not on active trial."""
        if not self.is_on_trial():
            return None
        expires = self._license_expires_naive()
        if not expires:
            return None
        total = max(0, int((expires - utcnow()).total_seconds()))
        days, rem = divmod(total, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, seconds = divmod(rem, 60)
        return days, hours, minutes, seconds

    def trial_remaining_label(self) -> str:
        """Human remaining trial time for admin listing (works offline from local clock)."""
        if self.is_trial_expired():
            return "Expired"
        parts = self.trial_remaining_parts()
        if not parts:
            return "—"
        days, hours, minutes, seconds = parts
        chunks: list[str] = []
        if days:
            chunks.append(f"{days}d")
        if hours or days:
            if hours or days < 2:
                chunks.append(f"{hours}h")
        if not days:
            chunks.append(f"{minutes}m")
            if not hours and minutes < 5:
                chunks.append(f"{seconds}s")
        if not chunks:
            chunks.append("0m")
        return " ".join(chunks) + " left"

    def is_registration_complete(self) -> bool:
        if self.role in PRIVILEGED_ROLES:
            return True
        if self.is_registered:
            return True
        # Active trial unlocks the app the same as registered (local SQLite clock — works offline)
        return self.is_on_trial()

    @property
    def registration_status_label(self) -> str:
        if self.role in PRIVILEGED_ROLES:
            return "Registered"
        if self.is_registered:
            if (self.license_type or "") == LICENSE_MONTHLY:
                return "Monthly"
            if (self.license_type or "") == LICENSE_LIFETIME:
                return "Lifetime"
            return "Registered"
        if self.is_on_trial():
            return f"Trial · {self.trial_remaining_label()}"
        if self.is_trial_expired():
            return "Trial Expired"
        return "Not Registered"

    @property
    def trial_ends_label(self) -> str:
        """Readable trial end for admin Users table (local datetime)."""
        if self.role in PRIVILEGED_ROLES or self.is_registered:
            return "—"
        expires = self._license_expires_naive()
        if not expires:
            return "—"
        text = expires.strftime("%Y-%m-%d %H:%M")
        if self.is_trial_expired():
            return f"{text}\nExpired"
        return f"{text}\n{self.trial_remaining_label()}"



class AuditLog(AgencyMixin, db.Model):
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


class Setting(AgencyMixin, db.Model):
    __tablename__ = "settings"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False, index=True)
    value = db.Column(db.Text, nullable=True)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)


class Notification(AgencyMixin, db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(50), default="info")
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    user = db.relationship("User", backref="notifications")


class AccountBalance(AgencyMixin, db.Model):
    """Per-agency rows for cash and bank."""

    __tablename__ = "account_balances"

    id = db.Column(db.Integer, primary_key=True)
    account_type = db.Column(db.String(20), unique=True, nullable=False)
    balance = db.Column(db.Numeric(14, 2), default=Decimal("0"), nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)


class CashBookEntry(AgencyMixin, db.Model):
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


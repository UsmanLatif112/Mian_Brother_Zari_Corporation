import enum
import secrets
from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from portal.extensions import db


def utcnow():
    return datetime.utcnow()


def generate_registration_key() -> str:
    parts = [secrets.token_hex(2).upper()[:4] for _ in range(3)]
    return f"MBZC-{'-'.join(parts)}"


class PortalRole(str, enum.Enum):
    SUPER_ADMIN = "super_admin"
    COMPANY_ADMIN = "company_admin"
    SHOP_CLERK = "shop_clerk"


class PortalUser(UserMixin, db.Model):
    __tablename__ = "portal_users"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(
        db.Integer, db.ForeignKey("portal_companies.id"), nullable=True, index=True
    )
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), nullable=True)
    full_name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(32), nullable=False, default=PortalRole.COMPANY_ADMIN.value)
    is_active_user = db.Column(db.Boolean, default=True, nullable=False)
    # Offline PC activation (Shop Clerk / Company Admin desktop package)
    registration_key = db.Column(db.String(32), nullable=True)
    is_registered = db.Column(db.Boolean, default=False, nullable=False)
    registered_at = db.Column(db.DateTime, nullable=True)
    device_id = db.Column(db.String(64), nullable=True)
    license_type = db.Column(db.String(20), nullable=True)
    license_expires_at = db.Column(db.DateTime, nullable=True)
    # After key: lifetime | 1_month | 1_year | custom
    subscription_plan = db.Column(db.String(20), nullable=True, default="lifetime")
    last_login_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    company = db.relationship("Company", back_populates="users")

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self) -> bool:
        if not self.is_active_user:
            return False
        if self.company_id and self.company and not self.company.is_active:
            return False
        return True

    def is_super_admin(self) -> bool:
        return self.role == PortalRole.SUPER_ADMIN.value

    def is_company_admin(self) -> bool:
        return self.role == PortalRole.COMPANY_ADMIN.value

    def is_shop_clerk(self) -> bool:
        return self.role == PortalRole.SHOP_CLERK.value

    def can_login_portal(self) -> bool:
        """Shop clerks use offline app only."""
        return self.role in {
            PortalRole.SUPER_ADMIN.value,
            PortalRole.COMPANY_ADMIN.value,
        }

    @property
    def role_label(self) -> str:
        return {
            PortalRole.SUPER_ADMIN.value: "Super Admin",
            PortalRole.COMPANY_ADMIN.value: "Company Admin",
            PortalRole.SHOP_CLERK.value: "Shop Clerk",
        }.get(self.role, self.role)

    def ensure_registration_key(self) -> str:
        if not self.registration_key:
            self.registration_key = generate_registration_key()
        return self.registration_key

    def regenerate_key(self) -> str:
        self.registration_key = generate_registration_key()
        self.is_registered = False
        self.registered_at = None
        self.device_id = None
        self.license_type = None
        self.license_expires_at = None
        return self.registration_key

"""Keep erp_user_registry in sync so the offline desktop app can activate PCs."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import text

from portal.extensions import db
from portal.models import PortalUser

REGISTRY_TABLE = "erp_user_registry"


def _is_sqlite() -> bool:
    try:
        return db.engine.dialect.name == "sqlite"
    except Exception:
        return False


def ensure_registry_table() -> None:
    if _is_sqlite():
        # Offline registry mirror is MySQL-only; skip on local SQLite.
        return
    sql = f"""
    CREATE TABLE IF NOT EXISTS {REGISTRY_TABLE} (
        id INT AUTO_INCREMENT PRIMARY KEY,
        username VARCHAR(80) NOT NULL UNIQUE,
        email VARCHAR(120) NULL,
        full_name VARCHAR(120) NOT NULL,
        role VARCHAR(32) NOT NULL,
        password_hash VARCHAR(256) NOT NULL,
        registration_key VARCHAR(32) NOT NULL,
        is_registered TINYINT(1) NOT NULL DEFAULT 0,
        registered_at DATETIME NULL,
        device_id VARCHAR(64) NULL,
        license_type VARCHAR(20) NULL,
        license_expires_at DATETIME NULL,
        company_name VARCHAR(200) NULL,
        company_logo VARCHAR(255) NULL,
        company_logo_data LONGBLOB NULL,
        company_id INT NULL,
        subscription_plan VARCHAR(20) NULL,
        is_active TINYINT(1) NOT NULL DEFAULT 1,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """
    db.session.execute(text(sql))
    for col, ddl in (
        ("company_id", "company_id INT NULL"),
        ("subscription_plan", "subscription_plan VARCHAR(20) NULL"),
    ):
        exists = db.session.execute(
            text(
                """
                SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = :t
                  AND COLUMN_NAME = :c
                """
            ),
            {"t": REGISTRY_TABLE, "c": col},
        ).scalar()
        if not exists:
            try:
                db.session.execute(text(f"ALTER TABLE {REGISTRY_TABLE} ADD COLUMN {ddl}"))
            except Exception:
                pass
    db.session.commit()


def _fmt(dt) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        if getattr(dt, "tzinfo", None) is not None:
            dt = dt.replace(tzinfo=None)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    return str(dt)[:19]


def mirror_user_to_registry(user: PortalUser) -> None:
    """Upsert portal user into erp_user_registry for offline activation."""
    if user.is_super_admin():
        return
    if _is_sqlite():
        return
    ensure_registry_table()
    company = user.company
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    key = user.registration_key or ""
    params = {
        "username": user.username,
        "email": user.email,
        "full_name": user.full_name,
        "role": "admin" if user.is_company_admin() else "sales",
        "password_hash": user.password_hash,
        "registration_key": key,
        "is_registered": 1 if user.is_registered else 0,
        "registered_at": _fmt(user.registered_at),
        "device_id": user.device_id,
        "license_type": user.license_type,
        "license_expires_at": _fmt(user.license_expires_at),
        "company_name": company.name if company else None,
        "company_logo": company.logo_path if company else None,
        "company_logo_data": company.logo_data if company else None,
        "company_id": user.company_id,
        "subscription_plan": getattr(user, "subscription_plan", None) or "lifetime",
        "is_active": 1 if user.is_active_user and (not company or company.is_active) else 0,
        "created_at": now,
        "updated_at": now,
    }
    existing = db.session.execute(
        text(
            f"SELECT id FROM {REGISTRY_TABLE} WHERE LOWER(username) = LOWER(:username)"
        ),
        {"username": user.username},
    ).fetchone()
    if existing:
        db.session.execute(
            text(
                f"""
                UPDATE {REGISTRY_TABLE} SET
                    email=:email, full_name=:full_name, role=:role,
                    password_hash=:password_hash, registration_key=:registration_key,
                    is_registered=:is_registered, registered_at=:registered_at,
                    device_id=:device_id, license_type=:license_type,
                    license_expires_at=:license_expires_at,
                    company_name=:company_name, company_logo=:company_logo,
                    company_logo_data=:company_logo_data, company_id=:company_id,
                    subscription_plan=:subscription_plan,
                    is_active=:is_active, updated_at=:updated_at
                WHERE username=:username
                """
            ),
            params,
        )
    else:
        db.session.execute(
            text(
                f"""
                INSERT INTO {REGISTRY_TABLE}
                (username, email, full_name, role, password_hash, registration_key,
                 is_registered, registered_at, device_id, license_type, license_expires_at,
                 company_name, company_logo, company_logo_data, company_id, subscription_plan,
                 is_active, created_at, updated_at)
                VALUES
                (:username, :email, :full_name, :role, :password_hash, :registration_key,
                 :is_registered, :registered_at, :device_id, :license_type, :license_expires_at,
                 :company_name, :company_logo, :company_logo_data, :company_id, :subscription_plan,
                 :is_active, :created_at, :updated_at)
                """
            ),
            params,
        )
    db.session.commit()

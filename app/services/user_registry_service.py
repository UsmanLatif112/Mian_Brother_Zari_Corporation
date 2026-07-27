"""Global user registry on MySQL (activation keys + registration status)."""

from __future__ import annotations

import logging
import os
import secrets
import uuid
from datetime import datetime
from typing import Any

from flask import current_app
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

REGISTRY_TABLE = "erp_user_registry"


def generate_registration_key() -> str:
    parts = [secrets.token_hex(2).upper()[:4] for _ in range(3)]
    return f"MBZC-{'-'.join(parts)}"


def _mysql_uri() -> str | None:
    uri = (os.environ.get("MYSQL_DATABASE_URI") or "").strip()
    return uri or None


def _engine() -> Engine | None:
    uri = _mysql_uri()
    if not uri:
        return None
    return create_engine(uri, pool_pre_ping=True, pool_recycle=28000)


def mysql_configured() -> bool:
    return bool(_mysql_uri())


def ensure_registry_table(engine: Engine | None = None) -> None:
    eng = engine or _engine()
    if not eng:
        raise ValueError("MySQL is not configured. Set MYSQL_DATABASE_URI in .env.")
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
        is_active TINYINT(1) NOT NULL DEFAULT 1,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """
    with eng.connect() as conn:
        conn.execute(text(sql))
        conn.commit()


def device_id() -> str:
    """Stable id for this PC (stored in instance folder)."""
    path = os.path.join(current_app.instance_path, "device_id.txt")
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                val = fh.read().strip()
                if val:
                    return val
        except OSError:
            pass
    val = str(uuid.uuid4())
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(val)
    return val


def save_user_to_mysql(user, *, registration_key: str) -> int:
    eng = _engine()
    if not eng:
        raise ValueError("MySQL is not configured.")
    ensure_registry_table(eng)
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    is_reg = 1 if getattr(user, "is_registered", False) else 0
    params = {
        "username": user.username,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role.value if hasattr(user.role, "value") else str(user.role),
        "password_hash": user.password_hash,
        "registration_key": registration_key,
        "is_registered": is_reg,
        "registered_at": user.registered_at,
        "device_id": user.device_id,
        "is_active": 1 if user.is_active_user else 0,
        "created_at": now,
        "updated_at": now,
    }
    with eng.connect() as conn:
        existing = conn.execute(
            text(f"SELECT id FROM {REGISTRY_TABLE} WHERE LOWER(username) = LOWER(:username)"),
            {"username": user.username},
        ).fetchone()
        if existing:
            conn.execute(
                text(
                    f"""
                    UPDATE {REGISTRY_TABLE} SET
                        email=:email, full_name=:full_name, role=:role,
                        password_hash=:password_hash, registration_key=:registration_key,
                        is_registered=:is_registered, registered_at=:registered_at,
                        device_id=:device_id, is_active=:is_active, updated_at=:updated_at
                    WHERE username=:username
                    """
                ),
                params,
            )
            cloud_id = int(existing[0])
        else:
            result = conn.execute(
                text(
                    f"""
                    INSERT INTO {REGISTRY_TABLE}
                    (username, email, full_name, role, password_hash, registration_key,
                     is_registered, registered_at, device_id, is_active, created_at, updated_at)
                    VALUES
                    (:username, :email, :full_name, :role, :password_hash, :registration_key,
                     :is_registered, :registered_at, :device_id, :is_active, :created_at, :updated_at)
                    """
                ),
                params,
            )
            cloud_id = int(result.lastrowid)
        conn.commit()
    return cloud_id


def activate_user_on_mysql(username: str, key: str, machine_id: str) -> dict[str, Any]:
    """Verify registration key in MySQL and mark user registered (one PC per key)."""
    eng = _engine()
    if not eng:
        raise ValueError("MySQL is not configured.")
    ensure_registry_table(eng)
    with eng.connect() as conn:
        row = conn.execute(
            text(
                f"""
                SELECT id, registration_key, is_registered, device_id
                FROM {REGISTRY_TABLE}
                WHERE LOWER(username) = LOWER(:username) AND is_active = 1
                """
            ),
            {"username": username},
        ).fetchone()
        if not row:
            return {"ok": False, "error": "User not found in cloud registry. Contact admin."}
        cloud_id, db_key, is_registered, bound_device = row
        if (db_key or "").strip().upper() != (key or "").strip().upper():
            return {"ok": False, "error": "Invalid registration key."}
        if is_registered and bound_device and bound_device != machine_id:
            return {"ok": False, "error": "This key is already used on another computer."}
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            text(
                f"""
                UPDATE {REGISTRY_TABLE}
                SET is_registered = 1, registered_at = :now, device_id = :device_id, updated_at = :now
                WHERE id = :id
                """
            ),
            {"now": now, "device_id": machine_id, "id": cloud_id},
        )
        conn.commit()
    return {"ok": True, "cloud_id": cloud_id, "registration_key": db_key}


def fetch_registry_user_full(username: str) -> dict[str, Any] | None:
    """Load full cloud registry row for login import."""
    eng = _engine()
    if not eng:
        return None
    try:
        ensure_registry_table(eng)
    except Exception:
        return None
    with eng.connect() as conn:
        row = conn.execute(
            text(
                f"""
                SELECT id, username, email, full_name, role, password_hash,
                       registration_key, is_registered, registered_at, device_id
                FROM {REGISTRY_TABLE}
                WHERE LOWER(username) = LOWER(:username) AND is_active = 1
                """
            ),
            {"username": username},
        ).fetchone()
    if not row:
        return None
    return {
        "cloud_id": row[0],
        "username": row[1],
        "email": row[2],
        "full_name": row[3],
        "role": row[4],
        "password_hash": row[5],
        "registration_key": row[6],
        "is_registered": bool(row[7]),
        "registered_at": row[8],
        "device_id": row[9],
    }


def import_user_from_mysql_registry(username: str, password: str):
    """
    First login on a new PC: pull user from MySQL into local SQLite.
    Returns a User model instance (not yet added to session) or None.
    """
    from werkzeug.security import check_password_hash

    from app.models import User
    from app.models.user import UserRole

    row = fetch_registry_user_full(username)
    if not row or not check_password_hash(row["password_hash"], password):
        return None
    try:
        role = UserRole(row["role"])
    except ValueError:
        role = UserRole.SALES
    return User(
        username=row["username"],
        email=row["email"] or f"{row['username']}@mbzc.local",
        password_hash=row["password_hash"],
        full_name=row["full_name"],
        role=role,
        is_active_user=True,
        is_registered=row["is_registered"],
        registration_key=row["registration_key"],
        registered_at=row["registered_at"],
        device_id=row["device_id"],
        remote_id=row["cloud_id"],
    )


def delete_user_from_mysql(username: str) -> bool:
    """Remove user row from cloud registry (hard delete)."""
    eng = _engine()
    if not eng:
        return True
    try:
        ensure_registry_table(eng)
    except Exception:
        logger.warning("Could not ensure registry table for delete", exc_info=True)
        return False
    with eng.connect() as conn:
        conn.execute(
            text(f"DELETE FROM {REGISTRY_TABLE} WHERE username = :username"),
            {"username": username},
        )
        conn.commit()
    return True


def deactivate_user_on_mysql(username: str) -> None:
    """Deprecated: use delete_user_from_mysql. Kept as alias for compatibility."""
    delete_user_from_mysql(username)


def update_password_on_mysql(username: str, password_hash: str) -> None:
    eng = _engine()
    if not eng:
        return
    try:
        ensure_registry_table(eng)
    except Exception:
        return
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    with eng.connect() as conn:
        conn.execute(
            text(
                f"""
                UPDATE {REGISTRY_TABLE}
                SET password_hash = :password_hash, updated_at = :now
                WHERE username = :username
                """
            ),
            {"password_hash": password_hash, "now": now, "username": username},
        )
        conn.commit()


def ensure_user_registration_columns() -> None:
    """Add registration columns to local SQLite users table (offline-safe)."""
    from sqlalchemy import inspect, text

    from app.extensions import db

    try:
        insp = inspect(db.engine)
        cols = {c["name"] for c in insp.get_columns("users")}
        alters = []
        if "is_registered" not in cols:
            alters.append(
                "ALTER TABLE users ADD COLUMN is_registered BOOLEAN DEFAULT 0 NOT NULL"
            )
        if "registration_key" not in cols:
            alters.append("ALTER TABLE users ADD COLUMN registration_key VARCHAR(32)")
        if "registered_at" not in cols:
            alters.append("ALTER TABLE users ADD COLUMN registered_at DATETIME")
        if "device_id" not in cols:
            alters.append("ALTER TABLE users ADD COLUMN device_id VARCHAR(64)")
        if alters:
            with db.engine.begin() as conn:
                for stmt in alters:
                    conn.execute(text(stmt))
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE users SET is_registered = 1
                    WHERE role IN ('super_admin', 'SUPER_ADMIN')
                      AND is_registered = 0
                    """
                )
            )
            conn.execute(
                text(
                    """
                    UPDATE users SET role = 'super_admin'
                    WHERE username = 'admin' AND role IN ('admin', 'ADMIN', 'SUPER_ADMIN')
                    """
                )
            )
            conn.execute(
                text(
                    """
                    UPDATE users SET role = 'admin'
                    WHERE role = 'ADMIN' AND username != 'admin'
                    """
                )
            )
    except Exception:
        logger.warning("Could not ensure user registration columns", exc_info=True)


def fetch_user_from_mysql(username: str) -> dict[str, Any] | None:
    eng = _engine()
    if not eng:
        return None
    try:
        ensure_registry_table(eng)
    except Exception:
        return None
    with eng.connect() as conn:
        row = conn.execute(
            text(
                f"""
                SELECT id, registration_key, is_registered, device_id
                FROM {REGISTRY_TABLE} WHERE username = :username
                """
            ),
            {"username": username},
        ).fetchone()
    if not row:
        return None
    return {
        "cloud_id": row[0],
        "registration_key": row[1],
        "is_registered": bool(row[2]),
        "device_id": row[3],
    }

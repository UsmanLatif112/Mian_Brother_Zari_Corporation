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
        license_type VARCHAR(20) NULL,
        license_expires_at DATETIME NULL,
        is_active TINYINT(1) NOT NULL DEFAULT 1,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """
    with eng.connect() as conn:
        conn.execute(text(sql))
        _ensure_mysql_license_columns(conn)
        conn.commit()


def _ensure_mysql_license_columns(conn) -> None:
    """Add license + branding columns to existing erp_user_registry tables."""
    for col_name, col_ddl in (
        ("license_type", "license_type VARCHAR(20) NULL"),
        ("license_expires_at", "license_expires_at DATETIME NULL"),
        ("company_name", "company_name VARCHAR(200) NULL"),
        ("company_logo", "company_logo VARCHAR(255) NULL"),
        ("company_logo_data", "company_logo_data LONGBLOB NULL"),
    ):
        exists = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = :table_name
                  AND COLUMN_NAME = :col_name
                """
            ),
            {"table_name": REGISTRY_TABLE, "col_name": col_name},
        ).scalar()
        if not exists:
            conn.execute(text(f"ALTER TABLE {REGISTRY_TABLE} ADD COLUMN {col_ddl}"))


def _fmt_mysql_dt(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if getattr(value, "tzinfo", None) is not None:
            value = value.replace(tzinfo=None)
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)[:19]

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
        "registered_at": _fmt_mysql_dt(getattr(user, "registered_at", None)),
        "device_id": user.device_id,
        "license_type": getattr(user, "license_type", None),
        "license_expires_at": _fmt_mysql_dt(getattr(user, "license_expires_at", None)),
        "company_name": getattr(user, "company_name", None),
        "company_logo": getattr(user, "company_logo", None),
        "company_logo_data": getattr(user, "company_logo_data", None),
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
                        device_id=:device_id, license_type=:license_type,
                        license_expires_at=:license_expires_at,
                        company_name=:company_name, company_logo=:company_logo,
                        company_logo_data=:company_logo_data,
                        is_active=:is_active, updated_at=:updated_at
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
                     is_registered, registered_at, device_id, license_type, license_expires_at,
                     company_name, company_logo, company_logo_data,
                     is_active, created_at, updated_at)
                    VALUES
                    (:username, :email, :full_name, :role, :password_hash, :registration_key,
                     :is_registered, :registered_at, :device_id, :license_type, :license_expires_at,
                     :company_name, :company_logo, :company_logo_data,
                     :is_active, :created_at, :updated_at)
                    """
                ),
                params,
            )
            cloud_id = int(result.lastrowid)
        conn.commit()
    return cloud_id


def activate_user_on_mysql(username: str, key: str, machine_id: str) -> dict[str, Any]:
    """Verify registration key in MySQL and mark user lifetime-registered (one PC per key)."""
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
                SET is_registered = 1,
                    registered_at = :now,
                    device_id = :device_id,
                    license_type = 'lifetime',
                    license_expires_at = NULL,
                    updated_at = :now
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
                       registration_key, is_registered, registered_at, device_id,
                       license_type, license_expires_at,
                       company_name, company_logo, company_logo_data
                FROM {REGISTRY_TABLE}
                WHERE LOWER(username) = LOWER(:username) AND is_active = 1
                """
            ),
            {"username": username},
        ).fetchone()
    if not row:
        return None
    logo_blob = row[14]
    if logo_blob is not None and not isinstance(logo_blob, (bytes, memoryview)):
        try:
            logo_blob = bytes(logo_blob)
        except Exception:
            logo_blob = None
    elif isinstance(logo_blob, memoryview):
        logo_blob = logo_blob.tobytes()
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
        "license_type": row[10],
        "license_expires_at": row[11],
        "company_name": row[12],
        "company_logo": row[13],
        "company_logo_data": logo_blob,
    }


def import_user_from_mysql_registry(username: str, password: str):
    """
    First login on a new PC: pull user from MySQL into local SQLite.
    Returns a User model instance (not yet added to session) or None.
    """
    from werkzeug.security import check_password_hash

    from app.models import User
    from app.models.user import UserRole
    from app.utils.company_branding import materialize_logo_from_blob

    row = fetch_registry_user_full(username)
    if not row or not check_password_hash(row["password_hash"], password):
        return None
    try:
        role = UserRole(row["role"])
    except ValueError:
        role = UserRole.SALES
    user = User(
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
        license_type=row.get("license_type"),
        license_expires_at=row.get("license_expires_at"),
        company_name=row.get("company_name"),
        company_logo=row.get("company_logo"),
        company_logo_data=row.get("company_logo_data"),
    )
    try:
        materialize_logo_from_blob(user)
    except Exception:
        logger.warning("Could not materialize company logo for %s", username, exc_info=True)
    return user


def delete_user_from_mysql(username: str) -> bool:
    """Remove user row from cloud registry (hard delete). Returns True if removed or none existed."""
    eng = _engine()
    if not eng:
        return True
    try:
        ensure_registry_table(eng)
    except Exception:
        logger.warning("Could not ensure registry table for delete", exc_info=True)
        return False
    try:
        with eng.connect() as conn:
            result = conn.execute(
                text(
                    f"DELETE FROM {REGISTRY_TABLE} WHERE LOWER(username) = LOWER(:username)"
                ),
                {"username": username},
            )
            conn.commit()
            logger.info(
                "Deleted %s cloud registry row(s) for username=%s",
                result.rowcount,
                username,
            )
        return True
    except Exception:
        logger.warning("MySQL user delete failed for %s", username, exc_info=True)
        return False


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
        if "license_type" not in cols:
            alters.append("ALTER TABLE users ADD COLUMN license_type VARCHAR(20)")
        if "license_expires_at" not in cols:
            alters.append("ALTER TABLE users ADD COLUMN license_expires_at DATETIME")
        if "company_name" not in cols:
            alters.append("ALTER TABLE users ADD COLUMN company_name VARCHAR(200)")
        if "company_logo" not in cols:
            alters.append("ALTER TABLE users ADD COLUMN company_logo VARCHAR(255)")
        if "company_logo_data" not in cols:
            alters.append("ALTER TABLE users ADD COLUMN company_logo_data BLOB")
        if alters:
            with db.engine.begin() as conn:
                for stmt in alters:
                    conn.execute(text(stmt))
        # Backfill logo bytes from disk for existing path-only rows
        try:
            from app.models import User
            from app.utils.company_branding import sync_logo_blob_from_disk

            dirty = False
            for u in User.query.filter(User.company_logo.isnot(None)).all():
                if u.company_logo_data:
                    continue
                sync_logo_blob_from_disk(u)
                if u.company_logo_data:
                    dirty = True
            if dirty:
                db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception("Logo blob backfill skipped")
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


def list_registry_users() -> list[dict[str, Any]]:
    """All active rows from MySQL erp_user_registry (registration status source of truth)."""
    eng = _engine()
    if not eng:
        raise ValueError("MySQL is not configured.")
    ensure_registry_table(eng)
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT id, username, registration_key, is_registered,
                       registered_at, device_id, is_active,
                       license_type, license_expires_at
                FROM {REGISTRY_TABLE}
                WHERE is_active = 1
                ORDER BY username
                """
            )
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "cloud_id": int(row[0]),
                "username": row[1],
                "registration_key": row[2],
                "is_registered": bool(row[3]),
                "registered_at": row[4],
                "device_id": row[5],
                "is_active": bool(row[6]),
                "license_type": row[7],
                "license_expires_at": row[8],
            }
        )
    return out


def pull_registration_status_to_local() -> dict[str, Any]:
    """
    Fetch registration status + trial intervals from MySQL into local SQLite.
    Does not create new local users — only refreshes matching usernames.
    """
    from app.extensions import db
    from app.models import User
    from app.models.user import PRIVILEGED_ROLES

    remote_rows = list_registry_users()
    by_username = {str(r["username"]).lower(): r for r in remote_rows}

    updated = 0
    unchanged = 0
    missing_on_cloud = 0
    details: list[dict[str, Any]] = []

    def _naive(dt):
        if dt is None:
            return None
        if hasattr(dt, "replace") and getattr(dt, "tzinfo", None):
            return dt.replace(tzinfo=None)
        return dt

    local_users = User.query.filter_by(is_deleted=False).all()
    for user in local_users:
        if user.role in PRIVILEGED_ROLES:
            unchanged += 1
            continue
        remote = by_username.get((user.username or "").lower())
        if not remote:
            missing_on_cloud += 1
            details.append(
                {
                    "username": user.username,
                    "status": "missing_on_cloud",
                    "local": user.registration_status_label,
                }
            )
            continue

        before = user.registration_status_label
        after_reg = bool(remote["is_registered"])
        changed = False

        if user.remote_id != remote["cloud_id"]:
            user.remote_id = remote["cloud_id"]
            changed = True
        if remote.get("registration_key") and (user.registration_key or "") != (
            remote.get("registration_key") or ""
        ):
            user.registration_key = remote["registration_key"]
            changed = True
        if bool(user.is_registered) != after_reg:
            user.is_registered = after_reg
            changed = True

        # Always sync trial / license interval from cloud
        remote_lt = remote.get("license_type")
        remote_exp = _naive(remote.get("license_expires_at"))
        if remote_lt is not None and user.license_type != remote_lt:
            user.license_type = remote_lt or None
            changed = True
        if user.license_expires_at != remote_exp:
            user.license_expires_at = remote_exp
            changed = True

        if after_reg:
            remote_at = _naive(remote.get("registered_at"))
            if remote_at is not None and user.registered_at != remote_at:
                user.registered_at = remote_at
                changed = True
            if remote.get("device_id") and user.device_id != remote["device_id"]:
                user.device_id = remote["device_id"]
                changed = True
        else:
            if user.registered_at is not None and not remote.get("registered_at"):
                user.registered_at = None
                changed = True
            if user.device_id and not remote.get("device_id"):
                user.device_id = None
                changed = True

        if changed:
            updated += 1
            details.append(
                {
                    "username": user.username,
                    "status": "updated",
                    "from": before,
                    "to": user.registration_status_label,
                }
            )
        else:
            unchanged += 1

    db.session.flush()
    return {
        "ok": True,
        "cloud_count": len(remote_rows),
        "updated": updated,
        "unchanged": unchanged,
        "missing_on_cloud": missing_on_cloud,
        "details": details,
    }


def regenerate_registration_key_for_user(user) -> str:
    """
    Issue a new registration key and clear device binding so the user can activate again.
    Updates local SQLite fields; caller should commit and call save_user_to_mysql when online.
    """
    from app.models.user import LICENSE_LIFETIME, LICENSE_TRIAL

    key = generate_registration_key()
    user.registration_key = key
    user.is_registered = False
    user.registered_at = None
    user.device_id = None
    # Lifetime licenses must re-activate with the new key
    if (user.license_type or "") == LICENSE_LIFETIME:
        user.license_type = None
        user.license_expires_at = None
    elif (user.license_type or "") == LICENSE_TRIAL:
        # Keep trial interval so admin still sees expired / days left
        pass
    return key


def push_local_license_to_mysql(user) -> int | None:
    """Push one local user's key + trial/lifetime license fields to MySQL."""
    if not mysql_configured():
        return None
    key = user.registration_key or generate_registration_key()
    if not user.registration_key:
        user.registration_key = key
    return save_user_to_mysql(user, registration_key=key)


def sync_registration_bidirectional() -> dict[str, Any]:
    """
    Pull MySQL registration/trial status into local SQLite, then push local
    license_type / license_expires_at / keys back so both DBs stay aligned.
    """
    from app.extensions import db
    from app.models import User
    from app.models.user import PRIVILEGED_ROLES

    pull = pull_registration_status_to_local()
    pushed = 0
    push_errors: list[str] = []

    if mysql_configured():
        for user in User.query.filter_by(is_deleted=False).all():
            if user.role in PRIVILEGED_ROLES:
                continue
            if not user.registration_key and not user.license_type:
                continue
            try:
                push_local_license_to_mysql(user)
                pushed += 1
            except Exception as exc:
                push_errors.append(f"{user.username}: {exc}")

    db.session.flush()
    return {
        **pull,
        "pushed": pushed,
        "push_errors": push_errors,
    }

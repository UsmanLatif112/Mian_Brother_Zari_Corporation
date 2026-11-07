import enum
import json
import logging
import os
import threading
import time
from datetime import date, datetime
from decimal import Decimal

from flask import current_app
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.mysql import insert as mysql_insert

from app.extensions import db
from app.models import (
    Customer,
    CustomerType,
    LedgerEntry,
    SyncLog,
    SyncQueue,
    User,
    Vendor,
)
from app.models.mixins import utcnow

logger = logging.getLogger(__name__)

# Online portal only needs customers, vendors, and their ledgers (+ user logo branding).
# Do not push inventory/sales/purchases/expenses/etc. — offline SQLite stays the full ERP.
SYNC_MODELS = [
    CustomerType,  # FK parent for customers when present
    User,  # company_name / company_logo_data for portal branding
    Customer,
    Vendor,
    LedgerEntry,  # party_type customer|vendor only (see _model_rows)
]

# Process-wide guards — keep UI / request threads free
_sync_lock = threading.Lock()
_mysql_cache = {"ok": False, "checked_at": 0.0}
_MYSQL_CACHE_TTL_SEC = 45.0
_MYSQL_CONNECT_TIMEOUT_SEC = 10


def is_local_sqlite() -> bool:
    uri = current_app.config.get("SQLALCHEMY_DATABASE_URI") or ""
    return uri.startswith("sqlite")


def get_sync_mysql_uri() -> str | None:
    target = os.environ.get("SYNC_MYSQL_TARGET", "production").lower()
    if target == "test":
        return os.environ.get("MYSQL_TEST_DATABASE_URI")
    return os.environ.get("MYSQL_DATABASE_URI")


def get_sync_target_label() -> str:
    target = os.environ.get("SYNC_MYSQL_TARGET", "production").lower()
    if target == "test":
        return "MySQL (test)"
    return "MySQL (production)"


def is_mysql_available(force: bool = False, timeout: float | None = None) -> bool:
    """
    Fast reachability check with short TCP timeout + short-lived cache.

    Cached results avoid slowing page loads / frequent scheduler probes.
    """
    now = time.monotonic()
    ttl = float(current_app.config.get("SYNC_MYSQL_CACHE_SECONDS", _MYSQL_CACHE_TTL_SEC))
    if not force and (now - _mysql_cache["checked_at"]) < ttl:
        return bool(_mysql_cache["ok"])

    uri = get_sync_mysql_uri()
    if not uri:
        _mysql_cache.update(ok=False, checked_at=now)
        return False

    connect_timeout = int(
        timeout
        if timeout is not None
        else current_app.config.get("SYNC_MYSQL_CONNECT_TIMEOUT", _MYSQL_CONNECT_TIMEOUT_SEC)
    )
    engine = None
    ok = False
    try:
        engine = create_engine(
            uri,
            pool_pre_ping=True,
            pool_size=1,
            max_overflow=0,
            connect_args={"connect_timeout": max(1, connect_timeout)},
        )
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        ok = True
    except Exception as exc:
        # Debug level — probes are frequent; avoid flooding logs when offline
        logger.debug("MySQL unavailable: %s", exc)
        ok = False
    finally:
        if engine is not None:
            try:
                engine.dispose()
            except Exception:
                pass

    _mysql_cache.update(ok=ok, checked_at=now)
    return ok


def enqueue_sync(entity_type, entity_id, operation, payload=None):
    item = SyncQueue(
        entity_type=entity_type,
        entity_id=entity_id,
        operation=operation,
        payload=json.dumps(payload) if payload else None,
    )
    db.session.add(item)


def _serialize_value(value):
    if value is None:
        return None
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value
    if isinstance(value, Decimal):
        return value
    return value


def _model_rows(model):
    if model is LedgerEntry:
        # Portal ledgers are customer/vendor only
        q = model.query.filter(model.party_type.in_(("customer", "vendor")))
        if hasattr(model, "is_deleted"):
            q = q.filter(model.is_deleted.is_(False))
        return q.order_by(model.id).all()
    if hasattr(model, "is_deleted"):
        return model.query.filter(model.is_deleted.is_(False)).all()
    return model.query.all()


def _push_model(conn, model) -> int:
    table = model.__table__
    count = 0
    for obj in _model_rows(model):
        data = {
            col.name: _serialize_value(getattr(obj, col.name))
            for col in table.columns
        }
        update_cols = {k: data[k] for k in data if k != "id"}
        stmt = mysql_insert(table).values(**data)
        stmt = stmt.on_duplicate_key_update(**update_cols)
        conn.execute(stmt)
        count += 1
    return count


def _prepare_user_logos_for_push() -> int:
    """Ensure company_logo disk files are loaded into company_logo_data before MySQL push."""
    from app.utils.company_branding import sync_logo_blob_from_disk

    updated = 0
    for user in User.query.filter(User.is_deleted.is_(False)).all():
        path = getattr(user, "company_logo", None)
        blob = getattr(user, "company_logo_data", None)
        if path and not blob:
            sync_logo_blob_from_disk(user)
            if getattr(user, "company_logo_data", None):
                updated += 1
    if updated:
        db.session.commit()
    return updated


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        text(
            """
            SELECT COUNT(*) FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t
            """
        ),
        {"t": table},
    ).scalar()
    return bool(row)


def _push_portal_branding(conn) -> int:
    """
    Copy desktop company logo/name into Agri Books portal tables so company
    admins see their logo (and favicon) online after Sync.
    """
    if not _table_exists(conn, "portal_companies") or not _table_exists(conn, "portal_users"):
        return 0

    updated = 0
    users = (
        User.query.filter(User.is_deleted.is_(False))
        .filter(
            (User.company_logo_data.isnot(None))
            | (User.company_logo.isnot(None))
            | (User.company_name.isnot(None))
        )
        .all()
    )
    for user in users:
        username = (user.username or "").strip()
        if not username:
            continue
        logo_path = getattr(user, "company_logo", None)
        logo_data = getattr(user, "company_logo_data", None)
        company_name = (getattr(user, "company_name", None) or "").strip() or None

        # Prefer matching portal user by username → company_id
        portal_row = conn.execute(
            text(
                """
                SELECT company_id FROM portal_users
                WHERE LOWER(username) = LOWER(:username)
                LIMIT 1
                """
            ),
            {"username": username},
        ).fetchone()
        company_id = portal_row[0] if portal_row else None

        if company_id is None and company_name:
            by_name = conn.execute(
                text(
                    """
                    SELECT id FROM portal_companies
                    WHERE LOWER(name) = LOWER(:name)
                    LIMIT 1
                    """
                ),
                {"name": company_name},
            ).fetchone()
            company_id = by_name[0] if by_name else None

        if company_id is not None:
            sets = ["last_sync_at = UTC_TIMESTAMP()"]
            params: dict = {"id": company_id}
            if company_name:
                sets.append("name = :name")
                params["name"] = company_name
            if logo_path is not None:
                sets.append("logo_path = :logo_path")
                params["logo_path"] = logo_path
            if logo_data is not None:
                sets.append("logo_data = :logo_data")
                params["logo_data"] = bytes(logo_data) if not isinstance(logo_data, (bytes, memoryview)) else logo_data
            conn.execute(
                text(f"UPDATE portal_companies SET {', '.join(sets)} WHERE id = :id"),
                params,
            )
            updated += 1

        if _table_exists(conn, "erp_user_registry"):
            reg_params = {
                "username": username,
                "company_name": company_name,
                "company_logo": logo_path,
                "company_logo_data": (
                    bytes(logo_data)
                    if logo_data is not None and not isinstance(logo_data, (bytes, memoryview))
                    else logo_data
                ),
                "company_id": company_id,
            }
            conn.execute(
                text(
                    """
                    UPDATE erp_user_registry SET
                        company_name = COALESCE(:company_name, company_name),
                        company_logo = COALESCE(:company_logo, company_logo),
                        company_logo_data = COALESCE(:company_logo_data, company_logo_data),
                        company_id = COALESCE(:company_id, company_id),
                        updated_at = UTC_TIMESTAMP()
                    WHERE LOWER(username) = LOWER(:username)
                    """
                ),
                reg_params,
            )
    return updated


def push_sqlite_to_mysql() -> tuple[int, str]:
    if not is_local_sqlite():
        raise RuntimeError(
            "Sync push only runs while the app uses local SQLite. "
            "Set OFFLINE_FIRST=true and DATABASE_MODE=sqlite."
        )
    mysql_uri = get_sync_mysql_uri()
    if not mysql_uri:
        raise RuntimeError("MySQL sync URI is not configured in .env")

    # Flush + WAL checkpoint so push reads a consistent local snapshot
    from app.services.backup_service import prepare_sqlite_for_export

    _prepare_user_logos_for_push()
    prepare_sqlite_for_export()

    connect_timeout = int(
        current_app.config.get("SYNC_MYSQL_CONNECT_TIMEOUT", _MYSQL_CONNECT_TIMEOUT_SEC)
    )
    remote_engine = create_engine(
        mysql_uri,
        pool_pre_ping=True,
        pool_size=1,
        max_overflow=0,
        connect_args={"connect_timeout": max(1, connect_timeout)},
    )
    try:
        for model in SYNC_MODELS:
            model.__table__.create(remote_engine, checkfirst=True)

        total = 0
        branding = 0
        with remote_engine.connect() as conn:
            conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            for model in SYNC_MODELS:
                total += _push_model(conn, model)
            conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
            try:
                branding = _push_portal_branding(conn)
            except Exception:
                logger.exception("Portal branding sync failed (data push still OK)")
            conn.commit()

        label = get_sync_target_label()
        if branding:
            return total, f"{label} (+{branding} portal logo/name update(s))"
        return total, label
    finally:
        remote_engine.dispose()


def _run_sync_body(user_id=None) -> SyncLog:
    log = SyncLog(direction="push", status="running", message="Sync started")
    db.session.add(log)
    db.session.flush()

    if not is_local_sqlite():
        log.status = "failed"
        log.message = "App is not using local SQLite. Offline sync is disabled."
        log.completed_at = utcnow()
        db.session.commit()
        return log

    if not is_mysql_available(force=True):
        log.status = "failed"
        log.message = (
            f"{get_sync_target_label()} is not reachable. "
            "Work offline and try again when internet is available."
        )
        log.completed_at = utcnow()
        db.session.commit()
        return log

    try:
        count, target = push_sqlite_to_mysql()
        pending = SyncQueue.query.filter_by(is_resolved=False).order_by(SyncQueue.id).all()
        for item in pending:
            item.is_resolved = True
            item.attempts += 1
        log.status = "success"
        log.records_synced = count
        log.message = (
            f"Pushed {count} customer/vendor ledger records from SQLite to {target}."
        )
    except Exception as exc:
        logger.exception("Sync failed")
        log.status = "failed"
        log.message = str(exc)

    log.completed_at = utcnow()
    db.session.commit()
    return log


def run_sync(user_id=None) -> SyncLog:
    """
    Manual sync (request thread). Waits briefly for the lock; if another sync
    is running, returns a 'busy' log without starting a second push.
    """
    acquired = _sync_lock.acquire(blocking=True, timeout=2)
    if not acquired:
        log = SyncLog(
            direction="push",
            status="failed",
            message="Sync already running in the background. Try again in a moment.",
            completed_at=utcnow(),
        )
        db.session.add(log)
        db.session.commit()
        return log
    try:
        return _run_sync_body(user_id=user_id)
    finally:
        _sync_lock.release()


def try_background_sync(reason: str = "auto") -> SyncLog | None:
    """
    Non-blocking sync for the scheduler. Returns None if skipped
    (already running, offline, or not SQLite) — never blocks the UI.
    """
    if not is_local_sqlite():
        return None
    if not is_mysql_available(force=False):
        logger.debug("Background sync skipped (%s): MySQL offline", reason)
        return None
    if not _sync_lock.acquire(blocking=False):
        logger.info("Background sync skipped (%s): already running", reason)
        return None
    try:
        logger.info("Background sync starting (%s)", reason)
        return _run_sync_body()
    finally:
        _sync_lock.release()

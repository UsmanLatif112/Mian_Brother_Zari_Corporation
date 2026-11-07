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
    Vendor,
)
from app.models.mixins import utcnow

logger = logging.getLogger(__name__)

# Online portal only needs customers, vendors, and their ledgers.
# Company logos are managed by Super Admin on the online portal (not synced from desktop).
# Do not push inventory/sales/purchases/expenses/etc. — offline SQLite stays the full ERP.
SYNC_MODELS = [
    CustomerType,  # FK parent for customers when present
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


def _touch_portal_company_sync(conn) -> int:
    """Stamp last_sync_at on portal companies linked to synced shop users (no logo push)."""
    if not _table_exists(conn, "portal_companies") or not _table_exists(conn, "portal_users"):
        return 0
    result = conn.execute(
        text(
            """
            UPDATE portal_companies
            SET last_sync_at = UTC_TIMESTAMP()
            WHERE id IN (SELECT DISTINCT company_id FROM portal_users WHERE company_id IS NOT NULL)
            """
        )
    )
    return int(result.rowcount or 0)


def _push_party_profit_metrics(conn) -> int:
    """
    Push all-time gross profit per customer/vendor for online portal list filters.
    Keeps sync lean — no full sales/inventory dump required on the portal.
    """
    from app.services.customer_list_analytics_service import customer_gross_profit_map
    from app.services.vendor_list_analytics_service import vendor_gross_profit_map

    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS portal_party_metrics (
              party_type VARCHAR(20) NOT NULL,
              party_id INT NOT NULL,
              gross_profit DECIMAL(14, 2) NOT NULL DEFAULT 0,
              updated_at DATETIME NULL,
              PRIMARY KEY (party_type, party_id)
            )
            """
        )
    )
    conn.execute(text("DELETE FROM portal_party_metrics"))

    count = 0
    customer_map = customer_gross_profit_map(period="all")
    for party_id, profit in customer_map.items():
        conn.execute(
            text(
                """
                INSERT INTO portal_party_metrics (party_type, party_id, gross_profit, updated_at)
                VALUES ('customer', :pid, :profit, UTC_TIMESTAMP())
                """
            ),
            {"pid": int(party_id), "profit": float(profit or 0)},
        )
        count += 1

    vendor_map = vendor_gross_profit_map(period="all")
    for party_id, profit in vendor_map.items():
        conn.execute(
            text(
                """
                INSERT INTO portal_party_metrics (party_type, party_id, gross_profit, updated_at)
                VALUES ('vendor', :pid, :profit, UTC_TIMESTAMP())
                """
            ),
            {"pid": int(party_id), "profit": float(profit or 0)},
        )
        count += 1
    return count


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
        with remote_engine.connect() as conn:
            conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            for model in SYNC_MODELS:
                total += _push_model(conn, model)
            conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
            try:
                _touch_portal_company_sync(conn)
            except Exception:
                logger.exception("Portal last_sync_at stamp failed (data push still OK)")
            try:
                metrics = _push_party_profit_metrics(conn)
                total += metrics
            except Exception:
                logger.exception("Portal party profit metrics sync failed (data push still OK)")
            conn.commit()

        return total, get_sync_target_label()
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

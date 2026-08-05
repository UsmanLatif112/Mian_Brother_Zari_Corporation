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
    AccountBalance,
    AccountAmountTaken,
    AccountCashSetup,
    AuditLog,
    CashBookEntry,
    Category,
    Customer,
    CustomerReceiving,
    Expense,
    ExpenseCategory,
    ExpenseSettlement,
    InventoryAdjustment,
    LedgerEntry,
    Notification,
    Product,
    Purchase,
    PurchaseItem,
    Sale,
    SaleItem,
    Setting,
    StockLayer,
    StockMovement,
    SyncLog,
    SyncQueue,
    Unit,
    UnitType,
    User,
    Vendor,
    VendorPayment,
)
from app.models.mixins import utcnow

logger = logging.getLogger(__name__)

# Parent tables before children (foreign keys)
SYNC_MODELS = [
    UnitType,
    Unit,
    ExpenseCategory,
    User,
    Setting,
    AccountBalance,
    Category,
    Product,
    Customer,
    Vendor,
    Expense,
    ExpenseSettlement,
    Purchase,
    PurchaseItem,
    Sale,
    SaleItem,
    CustomerReceiving,
    VendorPayment,
    LedgerEntry,
    StockLayer,
    StockMovement,
    InventoryAdjustment,
    CashBookEntry,
    AccountCashSetup,
    AccountAmountTaken,
    Notification,
    AuditLog,
]

# Process-wide guards — keep UI / request threads free
_sync_lock = threading.Lock()
_sync_cancel = threading.Event()
_progress_lock = threading.Lock()
_mysql_cache = {"ok": False, "checked_at": 0.0}
_MYSQL_CACHE_TTL_SEC = 45.0
_MYSQL_CONNECT_TIMEOUT_SEC = 2

# Live progress for Sync Now UI (0–100)
_sync_progress: dict = {
    "status": "idle",  # idle | running | success | failed | cancelled
    "percent": 0,
    "message": "",
    "detail": "",
    "table": "",
    "records_synced": 0,
    "total_records": 0,
    "tables_done": 0,
    "tables_total": 0,
}


class SyncCancelled(Exception):
    """Raised when user cancels a push in progress."""


def is_sync_lock_held() -> bool:
    return _sync_lock.locked()


def _check_sync_cancel() -> None:
    if _sync_cancel.is_set():
        raise SyncCancelled("Sync cancelled by user.")


def set_sync_progress(**kwargs) -> None:
    with _progress_lock:
        _sync_progress.update(kwargs)
        pct = _sync_progress.get("percent")
        if pct is not None:
            try:
                _sync_progress["percent"] = max(0, min(100, int(pct)))
            except (TypeError, ValueError):
                pass


def get_sync_progress() -> dict:
    with _progress_lock:
        return dict(_sync_progress)


def reset_sync_progress() -> None:
    set_sync_progress(
        status="idle",
        percent=0,
        message="",
        detail="",
        table="",
        records_synced=0,
        total_records=0,
        tables_done=0,
        tables_total=0,
    )


def is_local_sqlite() -> bool:
    uri = current_app.config.get("SQLALCHEMY_DATABASE_URI") or ""
    return uri.startswith("sqlite")


def get_sync_mysql_uri() -> str | None:
    target = os.environ.get("SYNC_MYSQL_TARGET", "test").lower()
    if target == "test":
        return os.environ.get("MYSQL_TEST_DATABASE_URI")
    return os.environ.get("MYSQL_DATABASE_URI")


def get_sync_target_label() -> str:
    target = os.environ.get("SYNC_MYSQL_TARGET", "test").lower()
    if target == "test":
        return "MySQL (cloud / test)"
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
    if model is Category:
        return (
            model.query.filter(model.is_deleted.is_(False))
            .order_by(model.parent_id.is_(None).desc(), model.id)
            .all()
        )
    if hasattr(model, "is_deleted"):
        return model.query.filter(model.is_deleted.is_(False)).all()
    return model.query.all()


def _is_mysql_lock_wait(exc: BaseException) -> bool:
    text_err = str(exc).lower()
    return (
        "1205" in text_err
        or "lock wait timeout" in text_err
        or "try restarting transaction" in text_err
        or "deadlock found" in text_err
    )


def _push_batch_size(model) -> int:
    """How many rows per multi-row UPSERT (fewer network round-trips)."""
    try:
        default = int(current_app.config.get("SYNC_PUSH_BATCH_SIZE", 200) or 200)
    except Exception:
        default = 200
    default = max(20, min(default, 500))
    # Rows with large BLOB fields (logos) — keep smaller batches
    table = model.__table__
    has_binary = False
    for col in table.columns:
        tname = type(col.type).__name__.lower()
        if "binary" in tname or "blob" in tname or "large" in tname:
            has_binary = True
            break
    if has_binary:
        return min(default, 40)
    return default


def _execute_stmt_with_retry(conn, stmt, *, label: str = "row") -> None:
    """Run one statement; retry briefly on InnoDB lock waits (1205 / deadlock)."""
    delays = (0.25, 0.75, 1.5)
    last_exc: BaseException | None = None
    for attempt, delay in enumerate([0.0, *delays], start=1):
        if delay:
            time.sleep(delay)
        try:
            _check_sync_cancel()
            conn.execute(stmt)
            return
        except SyncCancelled:
            raise
        except Exception as exc:
            last_exc = exc
            if not _is_mysql_lock_wait(exc):
                raise
            logger.warning(
                "MySQL lock wait on %s (attempt %s/%s): %s",
                label,
                attempt,
                len(delays) + 1,
                exc,
            )
    assert last_exc is not None
    raise last_exc


def _count_model_rows(model) -> int:
    """Count rows that will be pushed (matches _model_rows filters)."""
    if model is Category:
        return (
            model.query.filter(model.is_deleted.is_(False)).count()
        )
    if hasattr(model, "is_deleted"):
        return model.query.filter(model.is_deleted.is_(False)).count()
    return model.query.count()


def _push_model(conn, model, on_batch=None) -> int:
    """
    Push all rows for one model with multi-row INSERT ... ON DUPLICATE KEY UPDATE.
    one network round-trip per batch (default ~200 rows) instead of per row.
    on_batch(batch_row_count, table_name) is called after each successful batch.
    """
    table = model.__table__
    objects = _model_rows(model)
    if not objects:
        return 0

    col_names = [c.name for c in table.columns]
    rows = [
        {name: _serialize_value(getattr(obj, name)) for name in col_names}
        for obj in objects
    ]
    batch_size = _push_batch_size(model)
    total = 0
    for start in range(0, len(rows), batch_size):
        _check_sync_cancel()
        chunk = rows[start : start + batch_size]
        stmt = mysql_insert(table).values(chunk)
        update_map = {
            name: stmt.inserted[name]
            for name in col_names
            if name != "id"
        }
        if not update_map:
            update_map = {col_names[0]: stmt.inserted[col_names[0]]}
        stmt = stmt.on_duplicate_key_update(**update_map)
        _execute_stmt_with_retry(
            conn,
            stmt,
            label=f"{table.name}[{start}:{start + len(chunk)}]",
        )
        total += len(chunk)
        conn.commit()
        if on_batch:
            try:
                on_batch(len(chunk), table.name)
            except Exception:
                pass
    logger.info(
        "Pushed %s rows for %s (batch_size=%s)",
        total,
        table.name,
        batch_size,
    )
    return total


def _ensure_remote_columns(conn, model) -> None:
    """Add any model columns missing on the remote MySQL table (no drops)."""
    table = model.__table__
    table_name = table.name
    try:
        existing = {
            row[0]
            for row in conn.execute(text(f"SHOW COLUMNS FROM `{table_name}`")).fetchall()
        }
    except Exception:
        # Table will be created by create_all
        return
    if not existing:
        return
    for col in table.columns:
        if col.name in existing:
            continue
        try:
            col_type = col.type.compile(dialect=conn.dialect)
        except Exception:
            col_type = "TEXT"
        nullable = "NULL" if col.nullable else "NULL"  # additive only — always nullable safely
        try:
            conn.execute(
                text(
                    f"ALTER TABLE `{table_name}` ADD COLUMN `{col.name}` {col_type} {nullable}"
                )
            )
            conn.commit()  # DDL usually auto-commits; keep session clean
            logger.info("Remote table %s: added column %s", table_name, col.name)
        except Exception as exc:
            logger.warning(
                "Could not add column %s.%s: %s", table_name, col.name, exc
            )
            try:
                conn.rollback()
            except Exception:
                pass


def push_sqlite_to_mysql() -> tuple[int, str]:
    if not is_local_sqlite():
        raise RuntimeError(
            "Sync push only runs while the app uses local SQLite. "
            "Set OFFLINE_FIRST=true and DATABASE_MODE=sqlite."
        )
    mysql_uri = get_sync_mysql_uri()
    if not mysql_uri:
        raise RuntimeError("MySQL sync URI is not configured in .env")

    from app.services.backup_service import prepare_sqlite_for_export

    set_sync_progress(
        status="running",
        percent=2,
        message="Preparing local data…",
        detail="",
        table="",
        records_synced=0,
    )
    prepare_sqlite_for_export()
    _check_sync_cancel()

    try:
        from app.services.agency_service import backfill_agency_ids

        set_sync_progress(percent=4, message="Checking agency stamps…")
        backfill_agency_ids()
    except Exception:
        logger.debug("agency backfill before push skipped", exc_info=True)

    set_sync_progress(percent=6, message="Counting records…")
    table_counts: list[tuple] = []
    for model in SYNC_MODELS:
        try:
            table_counts.append((model, _count_model_rows(model)))
        except Exception:
            table_counts.append((model, 0))
    total_records = sum(n for _, n in table_counts)
    tables_total = len(SYNC_MODELS)
    set_sync_progress(
        percent=8,
        message="Connecting to cloud MySQL…",
        total_records=total_records,
        tables_total=tables_total,
        tables_done=0,
        records_synced=0,
    )

    connect_timeout = int(
        current_app.config.get("SYNC_MYSQL_CONNECT_TIMEOUT", _MYSQL_CONNECT_TIMEOUT_SEC)
    )
    remote_engine = create_engine(
        mysql_uri,
        pool_pre_ping=True,
        pool_size=1,
        max_overflow=0,
        isolation_level="READ COMMITTED",
        connect_args={
            "connect_timeout": max(1, connect_timeout),
            "read_timeout": int(
                current_app.config.get("SYNC_MYSQL_READ_TIMEOUT", 120) or 120
            ),
            "write_timeout": int(
                current_app.config.get("SYNC_MYSQL_WRITE_TIMEOUT", 120) or 120
            ),
        },
    )
    try:
        t0 = time.monotonic()
        set_sync_progress(percent=10, message="Preparing cloud tables…")
        db.metadata.create_all(remote_engine)

        total = 0
        rows_done = 0
        # Progress map: 10%…98% for row upload, 100% on success in caller
        upload_span = 88  # percent points for row upload phase

        with remote_engine.connect() as conn:
            try:
                conn.execute(text("SET SESSION innodb_lock_wait_timeout = 30"))
                conn.execute(text("SET SESSION transaction_isolation = 'READ-COMMITTED'"))
                conn.execute(text("SET SESSION unique_checks=0"))
                conn.execute(text("SET SESSION foreign_key_checks=0"))
            except Exception:
                logger.debug("Could not set MySQL session bulk options", exc_info=True)
                try:
                    conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
                except Exception:
                    pass
            conn.commit()

            for idx, (model, expected) in enumerate(table_counts):
                _check_sync_cancel()
                table_name = model.__table__.name
                set_sync_progress(
                    table=table_name,
                    tables_done=idx,
                    message=f"Uploading {table_name}…",
                    detail=(
                        f"Table {idx + 1} of {tables_total}"
                        + (f" · ~{expected} rows" if expected else "")
                    ),
                )
                try:
                    _ensure_remote_columns(conn, model)

                    def _on_batch(batch_n, tname, _idx=idx, _expected=expected):
                        nonlocal rows_done, total
                        rows_done += batch_n
                        denom = max(total_records, 1)
                        pct = 10 + int(upload_span * min(rows_done, denom) / denom)
                        set_sync_progress(
                            percent=min(98, pct),
                            records_synced=rows_done,
                            table=tname,
                            message=f"Uploading {tname}…",
                            detail=f"{rows_done} / {total_records or rows_done} records",
                        )

                    n = _push_model(conn, model, on_batch=_on_batch)
                    total += n
                    # If expected was 0 but we skipped, still move tables_done
                    if n == 0 and expected == 0:
                        # empty table — nudge progress by table proportion when no rows overall
                        if total_records == 0:
                            pct = 10 + int(upload_span * (idx + 1) / max(tables_total, 1))
                            set_sync_progress(percent=min(98, pct))
                    if n:
                        conn.commit()
                    set_sync_progress(tables_done=idx + 1)
                except SyncCancelled:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    raise
                except Exception:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    raise

            _check_sync_cancel()
            set_sync_progress(percent=99, message="Finishing…", detail="Restoring database settings")
            try:
                conn.execute(text("SET SESSION unique_checks=1"))
                conn.execute(text("SET SESSION foreign_key_checks=1"))
                conn.commit()
            except Exception:
                try:
                    conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
                    conn.commit()
                except Exception:
                    logger.debug("Could not restore MySQL session flags", exc_info=True)

        elapsed = time.monotonic() - t0
        logger.info(
            "MySQL push finished: %s records in %.1fs (%.0f rows/s) → %s",
            total,
            elapsed,
            (total / elapsed) if elapsed > 0 else 0,
            get_sync_target_label(),
        )
        set_sync_progress(
            records_synced=total,
            percent=99,
            message="Almost done…",
            detail=f"{total} records in {elapsed:.1f}s",
        )
        return total, get_sync_target_label()
    finally:
        remote_engine.dispose()


def prune_sync_logs(keep: int | None = None) -> None:
    """Keep only the newest N push log rows in sync_logs."""
    keep_n = keep
    if keep_n is None:
        keep_n = int(current_app.config.get("SYNC_LOG_KEEP", 5) or 5)
    keep_n = max(1, keep_n)
    ids = [
        row.id
        for row in SyncLog.query.order_by(SyncLog.id.desc()).limit(keep_n).all()
    ]
    if not ids:
        return
    SyncLog.query.filter(~SyncLog.id.in_(ids)).delete(synchronize_session=False)


def friendly_sync_message(message: str | None, status: str | None = None) -> str:
    """
    Customer-facing text for sync history / toasts.
    Technical SQLAlchemy/pymysql dumps stay in app logs only.
    """
    raw = (message or "").strip()
    if status == "success" and raw:
        return raw
    if not raw:
        return "Cloud sync failed. Your data is safe on this PC — try again later."

    lower = raw.lower()
    if "cancelled" in lower or "canceled" in lower:
        return raw

    technical_markers = (
        "sqlalchemy",
        "pymysql",
        "mysqldb",
        "operationalerror",
        "integrityerror",
        "programmingerror",
        "unknown column",
        "insert into",
        "update ",
        "[sql:",
        "traceback",
        "background on this error",
        "sqlalche.me",
        "(1054",
        "(1062",
        "(1146",
        "(1205",
        "lock wait timeout",
        "connection refused",
        "can't connect",
        "timeout",
        "access denied for user",
    )
    if any(m in lower for m in technical_markers) or len(raw) > 220:
        if "1205" in lower or "lock wait" in lower or "deadlock" in lower:
            return (
                "Cloud was busy (another sync or open connection). "
                "Wait 1-2 minutes, then try Sync Now again."
            )
        return (
            "Cloud sync failed. Your data is safe on this PC. "
            "Try Sync Now again, or contact admin if it keeps failing."
        )
    return raw


def close_running_sync_logs(message: str) -> int:
    """Mark every open 'running' log as failed/cancelled (stale or user cancel)."""
    rows = SyncLog.query.filter_by(status="running").all()
    if not rows:
        return 0
    now = utcnow()
    for log in rows:
        log.status = "failed"
        log.message = message
        log.completed_at = now
    prune_sync_logs()
    db.session.commit()
    return len(rows)


def request_cancel_sync() -> dict:
    """
    Ask the active push to stop, or clear stuck 'running' history if none is live.
    Cooperative: live worker stops at the next table/batch checkpoint.
    """
    lock_held = is_sync_lock_held()
    if lock_held:
        _sync_cancel.set()
        set_sync_progress(
            status="running",
            message="Cancel requested…",
            detail="Stopping after the current batch",
        )
        logger.info("Sync cancel requested (live push)")
        return {
            "ok": True,
            "lock_held": True,
            "closed_logs": 0,
            "message": (
                "Cancel requested. Wait a few seconds for the current step to stop, "
                "then use Sync Now."
            ),
        }

    closed = close_running_sync_logs(
        "Stopped stuck sync. You can Sync Now again."
    )
    _sync_cancel.clear()
    set_sync_progress(
        status="cancelled" if closed else "idle",
        percent=0 if not closed else get_sync_progress().get("percent") or 0,
        message="Stuck sync cleared" if closed else "No running sync",
        detail="",
    )
    logger.info("Sync cancel cleared stale logs (closed=%s)", closed)
    return {
        "ok": True,
        "lock_held": False,
        "closed_logs": closed,
        "message": (
            "Stuck sync cleared. You can Sync Now."
            if closed
            else "No running sync found. You can Sync Now."
        ),
    }


def is_sync_busy_for_ui() -> bool:
    """True if push lock is held or history still shows running rows."""
    if is_sync_lock_held():
        return True
    return SyncLog.query.filter_by(status="running").count() > 0


def _notify_sync_result(log: SyncLog) -> None:
    try:
        from app.services.toast_service import (
            notify_mysql_sync_failed,
            notify_mysql_sync_success,
        )

        if log.status == "success":
            notify_mysql_sync_success(
                friendly_sync_message(log.message, "success")
                or "MySQL cloud sync completed."
            )
        elif log.status == "failed":
            notify_mysql_sync_failed(friendly_sync_message(log.message, "failed"))
    except Exception:
        logger.debug("Sync toast skipped", exc_info=True)


def _run_sync_body(user_id=None) -> SyncLog:
    _sync_cancel.clear()
    set_sync_progress(
        status="running",
        percent=1,
        message="Starting sync…",
        detail="",
        table="",
        records_synced=0,
        total_records=0,
        tables_done=0,
        tables_total=len(SYNC_MODELS),
    )
    # Close orphaned "running" rows from a previous crash (lock is free in this thread)
    close_running_sync_logs(
        "Previous sync did not finish. Starting a new push."
    )

    log = SyncLog(direction="push", status="running", message="Sync started")
    db.session.add(log)
    db.session.flush()
    db.session.commit()  # visible while push runs

    if not is_local_sqlite():
        log.status = "failed"
        log.message = "App is not using local SQLite. Offline sync is disabled."
        log.completed_at = utcnow()
        prune_sync_logs()
        db.session.commit()
        set_sync_progress(
            status="failed",
            percent=100,
            message=friendly_sync_message(log.message, "failed"),
        )
        _notify_sync_result(log)
        return log

    if not is_mysql_available(force=True):
        log.status = "failed"
        log.message = (
            f"{get_sync_target_label()} is not reachable. "
            "Changes stay local and will auto-push when internet is available."
        )
        log.completed_at = utcnow()
        # Do not keep offline probe failures as permanent history rows
        db.session.delete(log)
        db.session.commit()
        set_sync_progress(
            status="failed",
            percent=0,
            message="Cloud MySQL is offline",
            detail=log.message,
        )
        return log

    try:
        _check_sync_cancel()
        count, target = push_sqlite_to_mysql()
        pending = SyncQueue.query.filter_by(is_resolved=False).order_by(SyncQueue.id).all()
        pending_n = len(pending)
        for item in pending:
            item.is_resolved = True
            item.attempts += 1
        log.status = "success"
        log.records_synced = count
        if pending_n:
            log.message = (
                f"Pushed {count} records to {target}. "
                f"Cleared {pending_n} queued change(s)."
            )
        else:
            log.message = f"Pushed {count} records from SQLite to {target}."
        set_sync_progress(
            status="success",
            percent=100,
            message=log.message,
            detail="Upload complete",
            records_synced=count,
            table="",
        )
    except SyncCancelled:
        logger.info("Sync cancelled by user")
        log.status = "failed"
        log.message = "Sync cancelled by user. You can Sync Now again."
        log.records_synced = 0
        set_sync_progress(
            status="cancelled",
            percent=get_sync_progress().get("percent") or 0,
            message=log.message,
            detail="Cancelled",
        )
    except Exception as exc:
        logger.exception("Sync failed: %s", exc)
        log.status = "failed"
        log.message = friendly_sync_message(str(exc), "failed")
        set_sync_progress(
            status="failed",
            percent=get_sync_progress().get("percent") or 0,
            message=log.message,
            detail="Failed",
        )
    finally:
        _sync_cancel.clear()

    log.completed_at = utcnow()
    prune_sync_logs()
    db.session.commit()
    # Toast success / real failures only; cancelled still notifies briefly
    _notify_sync_result(log)
    return log


def run_sync(user_id=None) -> SyncLog:
    """
    Manual sync (request thread). Waits briefly for the lock; if another sync
    is running, returns a non-persisted busy marker (use Cancel Sync first).
    """
    acquired = _sync_lock.acquire(blocking=True, timeout=2)
    if not acquired:
        # Transient object — not written to DB (avoids history spam)
        return SyncLog(
            direction="push",
            status="busy",
            message=(
                "Sync already running. Tap Cancel Sync, wait a few seconds, then Sync Now."
            ),
            completed_at=utcnow(),
        )
    try:
        return _run_sync_body(user_id=user_id)
    finally:
        _sync_lock.release()


def start_background_sync(user_id=None) -> dict:
    """
    Start push in a daemon thread so the browser can poll progress 0–100%.
    Returns immediately with status running | busy.
    """
    if not is_local_sqlite():
        return {
            "ok": False,
            "status": "failed",
            "started": False,
            "message": "App is not using local SQLite. Offline sync is disabled.",
        }
    if not _sync_lock.acquire(blocking=False):
        return {
            "ok": False,
            "status": "busy",
            "started": False,
            "message": (
                "Sync already running. Tap Cancel Sync, wait a few seconds, then Sync Now."
            ),
        }

    app = current_app._get_current_object()
    set_sync_progress(
        status="running",
        percent=1,
        message="Starting…",
        detail="",
        table="",
        records_synced=0,
        total_records=0,
        tables_done=0,
        tables_total=len(SYNC_MODELS),
    )

    def worker():
        try:
            with app.app_context():
                log = _run_sync_body(user_id=user_id)
                try:
                    if log.status != "busy" and getattr(log, "id", None):
                        from app.services.audit_service import log_audit

                        log_audit("sync", "database", log.id, log.message or "")
                        db.session.commit()
                except Exception:
                    logger.debug("audit after background sync skipped", exc_info=True)
        except Exception:
            logger.exception("Background sync worker crashed")
            set_sync_progress(
                status="failed",
                percent=0,
                message="Cloud sync failed unexpectedly.",
                detail="",
            )
        finally:
            try:
                _sync_lock.release()
            except RuntimeError:
                pass

    threading.Thread(target=worker, name="mysql-cloud-sync", daemon=True).start()
    return {
        "ok": True,
        "status": "running",
        "started": True,
        "message": "Cloud sync started",
        "progress": get_sync_progress(),
    }


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


def get_sync_status_for_ui() -> dict:
    """Summary for Backup & Cloud MySQL card."""
    auto_on = bool(current_app.config.get("SYNC_AUTO_ENABLED"))
    minutes = int(current_app.config.get("SYNC_INTERVAL_MINUTES", 15) or 15)
    keep = int(current_app.config.get("SYNC_LOG_KEEP", 5) or 5)
    online = is_mysql_available()
    pending_q = SyncQueue.query.filter_by(is_resolved=False).count()
    lock_held = is_sync_lock_held()
    running_count = SyncLog.query.filter_by(status="running").count()
    busy = lock_held or running_count > 0
    logs = SyncLog.query.order_by(SyncLog.id.desc()).limit(keep).all()
    last = logs[0] if logs else None
    display_logs = [
        {
            "id": log.id,
            "started_at": log.started_at,
            "completed_at": log.completed_at,
            "status": log.status,
            "records_synced": log.records_synced,
            "message": friendly_sync_message(log.message, log.status),
        }
        for log in logs
    ]
    return {
        "auto_enabled": auto_on,
        "interval_minutes": minutes,
        "target_label": get_sync_target_label(),
        "online": online,
        "pending_queue": pending_q,
        "logs": display_logs,
        "last_status": last.status if last else None,
        "last_message": (
            friendly_sync_message(last.message, last.status) if last else None
        ),
        "last_at": last.completed_at or last.started_at if last else None,
        "log_keep": keep,
        "local_sqlite": is_local_sqlite(),
        "configured": bool(get_sync_mysql_uri()),
        "can_manual_push": is_local_sqlite() and bool(get_sync_mysql_uri()),
        "is_running": busy,
        "lock_held": lock_held,
        "running_count": running_count,
        "progress": get_sync_progress(),
    }

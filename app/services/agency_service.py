"""
Shop / agency tenancy.

agency_id on every business row = the shop Admin user's id.
- Admin user: agency_id = their own id
- Manager/Sales/Accountant: agency_id = their shop Admin's id
- Super Admin: usually null (platform), unless they act in a shop context

Local offline SQLite: typically one shop DB → all rows share that Admin agency_id.
Online (APP_MODE=online): MySQL primary + global query filter by agency_id.
"""

from __future__ import annotations

import logging
import os

from flask import has_request_context
from sqlalchemy import event, inspect, text
from sqlalchemy.orm import Session, with_loader_criteria

from app.extensions import db

logger = logging.getLogger(__name__)


def is_online_mode() -> bool:
    """True when this process is the hosted browser ERP (MySQL agency multi-tenant)."""
    mode = (os.environ.get("APP_MODE") or "").strip().lower()
    if mode in ("online", "host", "cloud", "web"):
        return True
    if mode in ("desktop", "local", "offline"):
        return False
    # Infer production host without desktop flag
    if os.environ.get("DESKTOP_APP", "").lower() in ("1", "true", "yes"):
        return False
    if os.environ.get("FLASK_ENV", "").lower() == "production":
        offline = os.environ.get("OFFLINE_FIRST", "true").lower() == "true"
        return not offline
    return False


def online_requires_registered() -> bool:
    """Online portal: no trial-only access (must be registered or Super Admin)."""
    if not is_online_mode():
        return False
    v = os.environ.get("ONLINE_REQUIRE_REGISTERED", "true").lower()
    return v in ("1", "true", "yes")


def _role_value(role) -> str:
    if role is None:
        return ""
    return getattr(role, "value", None) or str(role)


def resolve_agency_id(user=None) -> int | None:
    """Agency id for the given user (or current_user)."""
    if user is None:
        if not has_request_context():
            return default_agency_id()
        try:
            from flask_login import current_user

            if not getattr(current_user, "is_authenticated", False):
                return default_agency_id()
            user = current_user
        except Exception:
            return default_agency_id()

    role = _role_value(getattr(user, "role", None)).lower()
    # Super Admin is platform-level (unless they already have an agency set)
    if role == "super_admin":
        aid = getattr(user, "agency_id", None)
        return int(aid) if aid else None

    # Shop Admin IS the agency
    if role == "admin":
        return int(user.id)

    # Staff: prefer column, else fall back to shop admin on this DB
    aid = getattr(user, "agency_id", None)
    if aid:
        return int(aid)
    return default_agency_id()


def default_agency_id() -> int | None:
    """
    Single-shop offline default: first Admin user id, else first non-super user.
    """
    from app.models import User
    from app.models.user import UserRole

    try:
        admin = (
            User.query.filter(
                User.role == UserRole.ADMIN,
                User.is_deleted.is_(False),
            )
            .order_by(User.id.asc())
            .first()
        )
        if admin:
            return int(admin.id)
        any_user = (
            User.query.filter(
                User.role != UserRole.SUPER_ADMIN,
                User.is_deleted.is_(False),
            )
            .order_by(User.id.asc())
            .first()
        )
        return int(any_user.id) if any_user else None
    except Exception:
        logger.debug("default_agency_id failed", exc_info=True)
        return None


def apply_agency(obj, user=None, force: bool = False) -> None:
    """Set agency_id on a model instance if missing (or always if force)."""
    if not hasattr(obj, "agency_id"):
        return
    if not force and getattr(obj, "agency_id", None) is not None:
        return
    aid = resolve_agency_id(user)
    if aid is not None:
        obj.agency_id = aid


def stamp_user_agency(user) -> None:
    """After User insert/update of role: keep users.agency_id consistent."""
    if user is None or not getattr(user, "id", None):
        return
    role = _role_value(getattr(user, "role", None)).lower()
    if role == "admin":
        user.agency_id = user.id
    elif role == "super_admin":
        # Platform account — no shop agency
        if user.agency_id is None:
            pass
    elif user.agency_id is None:
        # Staff without agency → attach to default shop admin
        user.agency_id = default_agency_id()


def agency_scoped_query(model, query=None, agency_id: int | None = None):
    """
    Filter a query by agency when an agency is known.
    Super Admin / no agency → no filter (all rows visible).
    """
    q = query if query is not None else model.query
    if not hasattr(model, "agency_id"):
        return q
    aid = agency_id if agency_id is not None else resolve_agency_id()
    if aid is None:
        return q
    return q.filter(model.agency_id == aid)


def agency_model_classes() -> list:
    """All mapped models that carry agency_id (AgencyMixin or explicit column)."""
    from app.models import (
        AccountAmountTaken,
        AccountBalance,
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
        Unit,
        UnitType,
        User,
        Vendor,
        VendorPayment,
    )
    from app.models.sync import SyncLog, SyncQueue

    return [
        User,
        UnitType,
        Unit,
        ExpenseCategory,
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
        SyncLog,
        SyncQueue,
    ]


def ensure_agency_id_columns() -> None:
    """ADD COLUMN agency_id on every known table (offline-safe, no drop)."""
    insp = inspect(db.engine)
    tables = set(insp.get_table_names())
    for model in agency_model_classes():
        table = getattr(model, "__tablename__", None)
        if not table or table not in tables:
            continue
        try:
            cols = {c["name"] for c in insp.get_columns(table)}
        except Exception:
            continue
        if "agency_id" in cols:
            continue
        try:
            with db.engine.begin() as conn:
                conn.execute(
                    text(f"ALTER TABLE `{table}` ADD COLUMN agency_id INTEGER")
                )
            logger.info("Added agency_id to table %s", table)
            try:
                with db.engine.begin() as conn:
                    # Index name must be unique per SQLite DB; keep short & unique
                    ix = f"ix_{table}_agency_id"[:60]
                    conn.execute(
                        text(
                            f"CREATE INDEX IF NOT EXISTS `{ix}` ON `{table}` (agency_id)"
                        )
                    )
            except Exception:
                logger.debug("Index on %s.agency_id skipped", table, exc_info=True)
        except Exception:
            logger.warning("Could not add agency_id to %s", table, exc_info=True)
        # refresh inspector cache between tables
        insp = inspect(db.engine)

    backfill_agency_ids()


def backfill_agency_ids() -> None:
    """Fill NULL agency_id rows (legacy data) from shop Admin."""
    tables = set(inspect(db.engine).get_table_names())
    if "users" not in tables:
        return

    try:
        with db.engine.begin() as conn:
            # Admins are agencies of themselves
            conn.execute(
                text(
                    """
                    UPDATE users
                    SET agency_id = id
                    WHERE agency_id IS NULL
                      AND (
                        role = 'admin'
                        OR role = 'ADMIN'
                        OR CAST(role AS TEXT) = 'admin'
                      )
                    """
                )
            )
            row = conn.execute(
                text(
                    """
                    SELECT id FROM users
                    WHERE agency_id IS NOT NULL
                       OR role = 'admin'
                       OR CAST(role AS TEXT) = 'admin'
                    ORDER BY
                      CASE WHEN role = 'admin' OR CAST(role AS TEXT) = 'admin' THEN 0 ELSE 1 END,
                      id
                    LIMIT 1
                    """
                )
            ).fetchone()
            default_id = None
            if row:
                default_id = row[0]
            if default_id is None:
                any_u = conn.execute(
                    text(
                        "SELECT id FROM users ORDER BY id LIMIT 1"
                    )
                ).fetchone()
                default_id = any_u[0] if any_u else None
            if default_id is None:
                return

            # Staff / remaining users without agency
            conn.execute(
                text(
                    """
                    UPDATE users
                    SET agency_id = :aid
                    WHERE agency_id IS NULL
                      AND CAST(role AS TEXT) NOT IN ('super_admin', 'SUPER_ADMIN')
                    """
                ),
                {"aid": default_id},
            )

            for model in agency_model_classes():
                table = model.__tablename__
                if table == "users" or table not in tables:
                    continue
                try:
                    cols = {
                        c["name"]
                        for c in inspect(db.engine).get_columns(table)
                    }
                except Exception:
                    continue
                if "agency_id" not in cols:
                    continue
                try:
                    conn.execute(
                        text(
                            f"UPDATE `{table}` SET agency_id = :aid "
                            f"WHERE agency_id IS NULL"
                        ),
                        {"aid": default_id},
                    )
                except Exception:
                    logger.debug("backfill %s failed", table, exc_info=True)
    except Exception:
        logger.warning("agency_id backfill failed", exc_info=True)


_listeners_registered = False


def register_agency_session_listeners() -> None:
    """Auto-stamp agency_id on new rows; online mode filters SELECTs by agency."""
    global _listeners_registered
    if _listeners_registered:
        return
    _listeners_registered = True

    @event.listens_for(Session, "before_flush")
    def _stamp_agency_before_flush(session, flush_context, instances):
        try:
            aid = resolve_agency_id()
        except Exception:
            aid = None
        if aid is None:
            try:
                aid = default_agency_id()
            except Exception:
                aid = None

        for obj in list(session.new):
            if not hasattr(obj, "agency_id"):
                continue
            # User handled specially after id known / role known
            if obj.__class__.__name__ == "User":
                continue
            if getattr(obj, "agency_id", None) is None and aid is not None:
                obj.agency_id = aid

        for obj in list(session.new) + list(session.dirty):
            if obj.__class__.__name__ != "User":
                continue
            try:
                if getattr(obj, "id", None) and _role_value(obj.role).lower() == "admin":
                    if obj.agency_id != obj.id:
                        obj.agency_id = obj.id
                elif (
                    getattr(obj, "agency_id", None) is None
                    and _role_value(obj.role).lower() != "super_admin"
                ):
                    fallback = aid or default_agency_id()
                    if fallback is not None:
                        obj.agency_id = fallback
            except Exception:
                pass

    @event.listens_for(Session, "do_orm_execute")
    def _filter_agency_online(execute_state):
        """
        Online multi-tenant: every SELECT on AgencyMixin-style models is scoped
        to the current user's agency_id. Super Admin sees all (no filter).
        """
        if not is_online_mode():
            return
        if not execute_state.is_select:
            return
        if execute_state.is_column_load or execute_state.is_relationship_load:
            return
        if execute_state.execution_options.get("skip_agency_filter"):
            return
        if not has_request_context():
            return
        try:
            from flask_login import current_user

            if not getattr(current_user, "is_authenticated", False):
                return
            if getattr(current_user, "is_super_admin", lambda: False)():
                return
            aid = resolve_agency_id(current_user)
            if aid is None:
                # No agency attached — return empty for business data
                aid = -1
        except Exception:
            return

        from app.models.mixins import AgencyMixin

        try:
            execute_state.statement = execute_state.statement.options(
                with_loader_criteria(
                    AgencyMixin,
                    lambda cls: cls.agency_id == aid,
                    include_aliases=True,
                    track_closure_variables=False,
                )
            )
        except Exception:
            logger.debug("agency filter not applied", exc_info=True)

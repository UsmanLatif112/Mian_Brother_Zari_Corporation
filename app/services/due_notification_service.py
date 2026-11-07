"""Customer receivable due-date reminders (sales credit + cash loans)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import inspect, text

from app.extensions import db
from app.models import Customer, CustomerReceiving, Notification, Sale
from app.models.sales import PaymentStatus
from app.services.toast_service import push_toast
from app.utils.working_date import get_working_date


STAGE_TOMORROW = "tomorrow"
STAGE_DUE = "due"
STAGE_OVERDUE = "overdue"

# Toast / UI severity
STAGE_META = {
    STAGE_TOMORROW: {
        "label": "Due tomorrow",
        "category": "success",  # greenish
        "toast": "success",
    },
    STAGE_DUE: {
        "label": "Due today",
        "category": "warning",  # yellow
        "toast": "warning",
    },
    STAGE_OVERDUE: {
        "label": "Overdue",
        "category": "danger",  # red
        "toast": "danger",
    },
}


def ensure_due_date_schema():
    """Add due_date columns and notification tracking fields on existing DBs."""

    def _add(table: str, column: str, ddl: str):
        try:
            insp = inspect(db.engine)
            cols = {c["name"] for c in insp.get_columns(table)}
        except Exception:
            return
        if column in cols:
            return
        try:
            with db.engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))
        except Exception:
            pass

    try:
        Notification.__table__.create(db.engine, checkfirst=True)
    except Exception:
        pass

    _add("sales", "due_date", "due_date DATE")
    _add("customer_receivings", "due_date", "due_date DATE")
    _add("notifications", "reference_type", "reference_type VARCHAR(50)")
    _add("notifications", "reference_id", "reference_id INTEGER")
    _add("notifications", "stage", "stage VARCHAR(20)")
    _add("notifications", "notify_date", "notify_date DATE")


def _d(value) -> Decimal:
    return Decimal(str(value or 0))


def _sale_remaining(sale: Sale) -> Decimal:
    grand = _d(sale.grand_total)
    paid = _d(sale.amount_paid)
    return max(Decimal("0"), grand - min(paid, grand))


def _stage_for(due: date, today: date) -> str | None:
    """
    tomorrow = due is next working day
    due = due today
    overdue = 1 or 2 days past due (continuous red window)
    """
    delta = (due - today).days
    if delta == 1:
        return STAGE_TOMORROW
    if delta == 0:
        return STAGE_DUE
    if delta in (-1, -2):
        return STAGE_OVERDUE
    return None


def _already_notified(ref_type: str, ref_id: int, stage: str, notify_date: date) -> bool:
    return (
        Notification.query.filter_by(
            reference_type=ref_type,
            reference_id=ref_id,
            stage=stage,
            notify_date=notify_date,
        ).first()
        is not None
    )


def _create_notification(
    *,
    title: str,
    message: str,
    stage: str,
    ref_type: str,
    ref_id: int,
    notify_date: date,
    toast: bool = True,
) -> Notification | None:
    if _already_notified(ref_type, ref_id, stage, notify_date):
        return None
    meta = STAGE_META[stage]
    row = Notification(
        title=title,
        message=message,
        category=meta["category"],
        is_read=False,
        reference_type=ref_type,
        reference_id=ref_id,
        stage=stage,
        notify_date=notify_date,
    )
    db.session.add(row)
    if toast:
        push_toast(f"{title}: {message}", meta["toast"], source="due")
    return row


def sync_due_notifications(*, push_toasts: bool = True) -> dict:
    """
    Create due reminders for the current working date.
    Only when customer balance is receivable (> 0). Skip zero / prepaid.
    """
    ensure_due_date_schema()
    today = get_working_date()
    created = 0

    # --- Unpaid / partial sales with due_date ---
    sales = (
        Sale.query.filter(
            Sale.due_date.isnot(None),
            Sale.customer_id.isnot(None),
            Sale.payment_status.in_([PaymentStatus.UNPAID, PaymentStatus.PARTIAL]),
        )
        .all()
    )
    for sale in sales:
        remaining = _sale_remaining(sale)
        if remaining <= 0:
            continue
        customer = db.session.get(Customer, sale.customer_id)
        if not customer or customer.is_deleted or _d(customer.balance) <= 0:
            continue
        stage = _stage_for(sale.due_date, today)
        if not stage:
            continue
        days_late = (today - sale.due_date).days
        if stage == STAGE_OVERDUE:
            detail = f"{days_late} day{'s' if days_late != 1 else ''} overdue"
        elif stage == STAGE_TOMORROW:
            detail = "due tomorrow"
        else:
            detail = "due today"
        inv = sale.invoice_no or f"Sale #{sale.id}"
        title = f"{STAGE_META[stage]['label']} · {customer.name}"
        message = f"{inv} · {detail} · receivable {float(remaining):,.2f}"
        if _create_notification(
            title=title,
            message=message,
            stage=stage,
            ref_type="sale",
            ref_id=sale.id,
            notify_date=today,
            toast=push_toasts,
        ):
            created += 1

    # --- Unpaid loans with due_date ---
    loans = (
        CustomerReceiving.query.filter(
            CustomerReceiving.payment_type == "loan",
            CustomerReceiving.due_date.isnot(None),
        ).all()
    )
    for loan in loans:
        customer = db.session.get(Customer, loan.customer_id)
        if not customer or customer.is_deleted or _d(customer.balance) <= 0:
            continue
        stage = _stage_for(loan.due_date, today)
        if not stage:
            continue
        days_late = (today - loan.due_date).days
        if stage == STAGE_OVERDUE:
            detail = f"{days_late} day{'s' if days_late != 1 else ''} overdue"
        elif stage == STAGE_TOMORROW:
            detail = "due tomorrow"
        else:
            detail = "due today"
        title = f"{STAGE_META[stage]['label']} · {customer.name}"
        message = f"Cash loan · {detail} · amount {float(_d(loan.amount)):,.2f}"
        if _create_notification(
            title=title,
            message=message,
            stage=stage,
            ref_type="customer_loan",
            ref_id=loan.id,
            notify_date=today,
            toast=push_toasts,
        ):
            created += 1

    if created:
        db.session.commit()
    return {"created": created, "date": today.isoformat()}


def list_due_notifications(*, limit: int = 30, unread_only: bool = False) -> list[dict]:
    ensure_due_date_schema()
    q = Notification.query.filter(
        Notification.reference_type.in_(["sale", "customer_loan"])
    )
    if unread_only:
        q = q.filter(Notification.is_read.is_(False))
    rows = q.order_by(Notification.created_at.desc()).limit(limit).all()
    out = []
    for n in rows:
        out.append(
            {
                "id": n.id,
                "title": n.title,
                "message": n.message,
                "category": n.category or "info",
                "stage": n.stage,
                "is_read": bool(n.is_read),
                "created_at": n.created_at.isoformat(timespec="seconds") if n.created_at else None,
                "notify_date": n.notify_date.isoformat() if n.notify_date else None,
                "reference_type": n.reference_type,
                "reference_id": n.reference_id,
            }
        )
    return out


def mark_notification_read(notification_id: int) -> bool:
    row = db.session.get(Notification, notification_id)
    if not row:
        return False
    row.is_read = True
    db.session.commit()
    return True


def mark_all_due_notifications_read() -> int:
    rows = (
        Notification.query.filter(
            Notification.reference_type.in_(["sale", "customer_loan"]),
            Notification.is_read.is_(False),
        ).all()
    )
    for r in rows:
        r.is_read = True
    db.session.commit()
    return len(rows)


def suggest_due_date_for_customer(customer_id: int | None, base_date: date | None = None) -> date | None:
    """Suggest due date from customer type due_days (if > 0)."""
    if not customer_id:
        return None
    customer = db.session.get(Customer, customer_id)
    if not customer:
        return None
    try:
        from app.services.customer_type_service import customer_type_due_days

        days = int(customer_type_due_days(customer.customer_type) or 0)
    except Exception:
        days = 0
    if days <= 0:
        return None
    base = base_date or get_working_date()
    return base + timedelta(days=days)

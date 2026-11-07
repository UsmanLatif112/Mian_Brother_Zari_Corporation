"""Customer types (Good / Bad / custom) — managed like expense categories."""
from __future__ import annotations

import re

from app.extensions import db
from app.models import Customer
from app.models.party import CustomerType

DEFAULT_TYPES = (
    ("good", "Good", 30),
    ("bad", "Bad", 0),
    ("1_year", "1 Year", 365),
    ("6_month", "6 Month", 180),
    ("late_pay", "Late Pay", 0),
)


def slugify_type_code(name: str) -> str:
    raw = (name or "").strip().lower()
    code = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    return (code or "type")[:60]


def ensure_customer_types_schema():
    """Create customer_types table and seed built-ins."""
    try:
        CustomerType.__table__.create(db.engine, checkfirst=True)
    except Exception:
        return
    try:
        seed_default_customer_types()
    except Exception:
        db.session.rollback()


def seed_default_customer_types():
    changed = False
    for idx, (code, name, due_days) in enumerate(DEFAULT_TYPES):
        row = CustomerType.query.filter_by(code=code).first()
        if row:
            if row.is_deleted:
                row.is_deleted = False
                row.deleted_at = None
                changed = True
            if row.sort_order != idx:
                row.sort_order = idx
                changed = True
            continue
        db.session.add(
            CustomerType(
                code=code,
                name=name,
                due_days=due_days,
                is_system=True,
                sort_order=idx,
            )
        )
        changed = True
    if changed:
        db.session.commit()


def list_customer_types(*, include_deleted: bool = False):
    q = CustomerType.query
    if not include_deleted:
        q = q.filter(CustomerType.is_deleted.is_(False))
    return q.order_by(CustomerType.sort_order.asc(), CustomerType.name.asc()).all()


def customer_type_choices():
    """List of (code, label) for selects / filters."""
    rows = list_customer_types()
    if not rows:
        return [(c, n) for c, n, _ in DEFAULT_TYPES]
    return [(r.code, r.name) for r in rows]


def customer_type_label(code: str | None) -> str:
    code = (code or "good").strip() or "good"
    row = (
        CustomerType.query.filter_by(code=code, is_deleted=False).first()
        or CustomerType.query.filter_by(code=code).first()
    )
    if row:
        return row.name
    for c, n, _ in DEFAULT_TYPES:
        if c == code:
            return n
    return code.replace("_", " ").title()


def customer_type_due_days(code: str | None) -> int:
    code = (code or "good").strip() or "good"
    row = CustomerType.query.filter_by(code=code, is_deleted=False).first()
    if row is not None:
        return int(row.due_days or 0)
    for c, _, days in DEFAULT_TYPES:
        if c == code:
            return days
    return 30


def _unique_code(base: str, exclude_id: int | None = None) -> str:
    code = slugify_type_code(base)
    n = 2
    while True:
        q = CustomerType.query.filter_by(code=code)
        if exclude_id:
            q = q.filter(CustomerType.id != exclude_id)
        existing = q.first()
        if not existing:
            return code
        code = f"{slugify_type_code(base)}_{n}"[:60]
        n += 1


def create_customer_type(name: str, due_days: int = 30, code: str | None = None):
    name = (name or "").strip()
    if not name:
        raise ValueError("Type name is required.")
    due = max(0, int(due_days or 0))
    wanted = slugify_type_code(code or name)
    # Reactivate soft-deleted same name/code
    existing = (
        CustomerType.query.filter(
            (CustomerType.code == wanted) | (CustomerType.name.ilike(name))
        )
        .order_by(CustomerType.id.asc())
        .first()
    )
    if existing:
        if existing.is_deleted:
            existing.is_deleted = False
            existing.deleted_at = None
            existing.name = name
            existing.due_days = due
            db.session.flush()
            return existing
        raise ValueError(f'Type "{existing.name}" already exists.')
    row = CustomerType(
        code=_unique_code(wanted),
        name=name,
        due_days=due,
        is_system=False,
        sort_order=100,
    )
    db.session.add(row)
    db.session.flush()
    return row


def update_customer_type(type_id: int, name: str, due_days: int = 30):
    row = db.session.get(CustomerType, type_id)
    if not row or row.is_deleted:
        raise ValueError("Customer type not found.")
    name = (name or "").strip()
    if not name:
        raise ValueError("Type name is required.")
    clash = (
        CustomerType.query.filter(
            CustomerType.is_deleted.is_(False),
            CustomerType.name.ilike(name),
            CustomerType.id != type_id,
        ).first()
    )
    if clash:
        raise ValueError(f'Type "{clash.name}" already exists.')
    row.name = name
    row.due_days = max(0, int(due_days or 0))
    db.session.flush()
    return row


def delete_customer_type(type_id: int):
    row = db.session.get(CustomerType, type_id)
    if not row or row.is_deleted:
        raise ValueError("Customer type not found.")
    used = (
        Customer.query.filter_by(is_deleted=False, customer_type=row.code).count()
    )
    if used:
        raise ValueError(
            f'Cannot delete "{row.name}" — used by {used} customer(s).'
        )
    row.soft_delete()
    db.session.flush()
    return row

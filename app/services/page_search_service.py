"""Scoped search for navbar API (one entity type per page scope)."""

from sqlalchemy import or_

from app.models import (
    Customer,
    Expense,
    Product,
    Purchase,
    Sale,
    User,
    Vendor,
)


def search_scoped(scope: str, q: str, limit: int = 15) -> list[dict]:
    scope = (scope or "global").strip().lower()
    q = (q or "").strip()
    if len(q) < 2:
        return []

    if scope == "sales":
        return _search_sales(q, limit)
    if scope == "inventory":
        return _search_products(q, limit)
    if scope == "purchases":
        return _search_purchases(q, limit)
    if scope == "expenses":
        return _search_expenses(q, limit)
    if scope == "customers":
        return _search_customers(q, limit)
    if scope == "vendors":
        return _search_vendors(q, limit)
    if scope == "users":
        return _search_users(q, limit)
    return _search_global(q, limit)


def _search_global(q: str, limit: int) -> list[dict]:
    out: list[dict] = []
    out.extend(_search_customers(q, min(5, limit)))
    out.extend(_search_products(q, min(5, limit)))
    out.extend(_search_sales(q, min(5, limit)))
    return out[:limit]


def _search_sales(q: str, limit: int) -> list[dict]:
    like = f"%{q}%"
    rows = (
        Sale.query.outerjoin(Customer)
        .filter(
            or_(
                Sale.invoice_no.ilike(like),
                Sale.notes.ilike(like),
                Customer.name.ilike(like),
            )
        )
        .order_by(Sale.sale_date.desc(), Sale.id.desc())
        .limit(limit)
        .all()
    )
    results = []
    for s in rows:
        party = s.customer.name if s.customer else "Walk-in"
        results.append(
            {
                "type": "sale",
                "id": s.id,
                "label": f"{s.invoice_no} · {party}",
                "url": f"/sales/{s.id}/invoice",
            }
        )
    return results


def _search_products(q: str, limit: int) -> list[dict]:
    like = f"%{q}%"
    rows = (
        Product.query.filter(
            Product.is_deleted.is_(False),
            or_(Product.name.ilike(like), Product.sku.ilike(like)),
        )
        .order_by(Product.name)
        .limit(limit)
        .all()
    )
    return [
        {
            "type": "product",
            "id": p.id,
            "label": p.name,
            "url": f"/inventory/{p.id}",
        }
        for p in rows
    ]


def _search_purchases(q: str, limit: int) -> list[dict]:
    like = f"%{q}%"
    rows = (
        Purchase.query.outerjoin(Vendor)
        .filter(
            or_(
                Purchase.invoice_no.ilike(like),
                Purchase.notes.ilike(like),
                Vendor.name.ilike(like),
            )
        )
        .order_by(Purchase.purchase_date.desc(), Purchase.id.desc())
        .limit(limit)
        .all()
    )
    results = []
    for p in rows:
        vendor = p.vendor.name if p.vendor else "—"
        results.append(
            {
                "type": "purchase",
                "id": p.id,
                "label": f"{p.invoice_no} · {vendor}",
                "url": "/purchases/",
            }
        )
    return results


def _search_expenses(q: str, limit: int) -> list[dict]:
    from app.models import ExpenseCategory

    like = f"%{q}%"
    rows = (
        Expense.query.outerjoin(ExpenseCategory)
        .filter(
            Expense.is_deleted.is_(False),
            or_(
                Expense.name.ilike(like),
                Expense.description.ilike(like),
                ExpenseCategory.name.ilike(like),
            ),
        )
        .order_by(Expense.expense_date.desc(), Expense.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "type": "expense",
            "id": e.id,
            "label": f"{e.name} ({e.amount})",
            "url": "/expenses/",
        }
        for e in rows
    ]


def _search_customers(q: str, limit: int) -> list[dict]:
    like = f"%{q}%"
    rows = (
        Customer.query.filter(
            Customer.is_deleted.is_(False),
            or_(
                Customer.name.ilike(like),
                Customer.phone.ilike(like),
                Customer.old_book_no.ilike(like),
            ),
        )
        .order_by(Customer.name)
        .limit(limit)
        .all()
    )
    return [
        {
            "type": "customer",
            "id": c.id,
            "label": c.name,
            "url": f"/customers/{c.id}",
        }
        for c in rows
    ]


def _search_vendors(q: str, limit: int) -> list[dict]:
    like = f"%{q}%"
    rows = (
        Vendor.query.filter(
            Vendor.is_deleted.is_(False),
            or_(Vendor.name.ilike(like), Vendor.phone.ilike(like)),
        )
        .order_by(Vendor.name)
        .limit(limit)
        .all()
    )
    return [
        {
            "type": "vendor",
            "id": v.id,
            "label": v.name,
            "url": f"/vendors/{v.id}",
        }
        for v in rows
    ]


def _search_users(q: str, limit: int) -> list[dict]:
    like = f"%{q}%"
    rows = (
        User.query.filter(
            or_(
                User.username.ilike(like),
                User.full_name.ilike(like),
                User.email.ilike(like),
            )
        )
        .order_by(User.username)
        .limit(limit)
        .all()
    )
    return [
        {
            "type": "user",
            "id": u.id,
            "label": u.full_name or u.username,
            "url": "/auth/users",
        }
        for u in rows
    ]

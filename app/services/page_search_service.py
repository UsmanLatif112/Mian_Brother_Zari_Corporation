"""Scoped / global search for navbar API."""

from sqlalchemy import or_
from urllib.parse import quote

from app.models import (
    AccountAmountTaken,
    Category,
    Customer,
    Expense,
    Product,
    Purchase,
    Sale,
    User,
    Vendor,
)


def search_scoped(scope: str, q: str, limit: int = 15) -> list[dict]:
    """Search by page scope. ``global`` searches across the app (dashboard)."""
    scope = (scope or "").strip().lower()
    q = (q or "").strip()
    if len(q) < 2 or not scope or scope == "none":
        return []

    if scope == "global":
        return _search_global(q, limit)

    handlers = {
        "sales": _search_sales,
        "inventory": _search_products,
        "purchases": _search_purchases,
        "expenses": _search_expenses,
        "customers": _search_customers,
        "vendors": _search_vendors,
        "users": _search_users,
    }
    handler = handlers.get(scope)
    if not handler:
        return []
    return handler(q, limit)


def _search_global(q: str, limit: int = 15) -> list[dict]:
    """Dashboard: mix of entities; each result links to its page/entry."""
    per = max(2, min(5, limit // 2 or 2))
    out: list[dict] = []
    out.extend(_search_customers(q, per))
    out.extend(_search_vendors(q, per))
    out.extend(_search_products(q, per))
    out.extend(_search_sales(q, per))
    out.extend(_search_purchases(q, per))
    out.extend(_search_expenses(q, per))
    out.extend(_search_categories(q, per))
    out.extend(_search_amount_taken(q, per))
    out.extend(_search_users(q, per))
    return out[:limit]


def _page_search_url(path: str, q: str) -> str:
    """List pages apply ``?search=`` into the navbar/DataTable on load."""
    return f"{path}?search={quote(q)}"


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
        term = (p.invoice_no or vendor or q).strip() or q
        results.append(
            {
                "type": "purchase",
                "id": p.id,
                "label": f"{p.invoice_no} · {vendor}",
                "url": _page_search_url("/purchases/", term),
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
            "url": _page_search_url("/expenses/", e.name or q),
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


def _search_categories(q: str, limit: int) -> list[dict]:
    like = f"%{q}%"
    rows = (
        Category.query.filter(
            Category.is_deleted.is_(False),
            or_(Category.name.ilike(like), Category.description.ilike(like)),
        )
        .order_by(Category.name)
        .limit(limit)
        .all()
    )
    results = []
    for c in rows:
        if c.parent_id:
            url = f"/categories/{c.parent_id}?search={quote(c.name)}"
            label = f"{c.name} (subcategory)"
        else:
            url = f"/categories/{c.id}"
            label = c.name
        results.append(
            {
                "type": "category",
                "id": c.id,
                "label": label,
                "url": url,
            }
        )
    return results


def _search_amount_taken(q: str, limit: int) -> list[dict]:
    like = f"%{q}%"
    rows = (
        AccountAmountTaken.query.filter(
            AccountAmountTaken.is_deleted.is_(False),
            or_(
                AccountAmountTaken.taken_by.ilike(like),
                AccountAmountTaken.notes.ilike(like),
            ),
        )
        .order_by(AccountAmountTaken.taken_date.desc(), AccountAmountTaken.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "type": "amount taken",
            "id": r.id,
            "label": f"{r.taken_by} · {r.amount} ({r.taken_date})",
            "url": _page_search_url("/account/", r.taken_by or q),
        }
        for r in rows
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
            "url": _page_search_url("/auth/users", u.username or q),
        }
        for u in rows
    ]

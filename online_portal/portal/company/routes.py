from functools import wraps

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import text

from portal.extensions import db

company_bp = Blueprint("company", __name__)


def company_admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if current_user.is_super_admin():
            flash("Use Super Admin console for company management.", "info")
            return redirect(url_for("admin.dashboard"))
        if not current_user.is_company_admin():
            flash("Company Admin access only.", "danger")
            return redirect(url_for("auth.login"))
        if not current_user.is_active:
            flash("Account inactive.", "danger")
            return redirect(url_for("auth.logout"))
        return view(*args, **kwargs)

    return wrapped


def _company_id() -> int | None:
    return current_user.company_id


def _table_exists(name: str) -> bool:
    if db.engine.dialect.name == "sqlite":
        row = db.session.execute(
            text("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=:n"),
            {"n": name},
        ).scalar()
        return bool(row)
    row = db.session.execute(
        text(
            """
            SELECT COUNT(*) FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :n
            """
        ),
        {"n": name},
    ).scalar()
    return bool(row)


def _column_exists(table: str, column: str) -> bool:
    if db.engine.dialect.name == "sqlite":
        rows = db.session.execute(text(f"PRAGMA table_info({table})")).mappings().all()
        return any(r["name"] == column for r in rows)
    row = db.session.execute(
        text(
            """
            SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :t AND COLUMN_NAME = :c
            """
        ),
        {"t": table, "c": column},
    ).scalar()
    return bool(row)


def _period_bounds():
    """Resolve period query args → (period, start_date, end_date) as date strings."""
    from datetime import date, timedelta

    period = (request.args.get("period") or "all").strip().lower()
    start = (request.args.get("start_date") or "").strip()
    end = (request.args.get("end_date") or "").strip()
    today = date.today()
    if period == "today":
        start = end = today.isoformat()
    elif period == "week":
        start = (today - timedelta(days=7)).isoformat()
        end = today.isoformat()
    elif period == "month":
        start = (today - timedelta(days=30)).isoformat()
        end = today.isoformat()
    elif period == "3_month":
        start = (today - timedelta(days=90)).isoformat()
        end = today.isoformat()
    elif period == "12_month":
        start = (today - timedelta(days=365)).isoformat()
        end = today.isoformat()
    elif period == "custom":
        pass
    else:
        period = "all"
        start = end = ""
    return period, start, end


def _period_context(clear_endpoint: str, **url_kwargs):
    period, start, end = _period_bounds()
    return {
        "selected_period": period,
        "start_date": start if period == "custom" else "",
        "end_date": end if period == "custom" else "",
        "clear_url": url_for(clear_endpoint, period="all", **url_kwargs),
    }


def _period_clause(column: str = "created_at"):
    """Return SQL fragment + params for optional date filters (default = all)."""
    period, start, end = _period_bounds()
    parts = []
    params = {}
    if period != "all" and start:
        parts.append(f"DATE({column}) >= :p_from")
        params["p_from"] = start
    if period != "all" and end:
        parts.append(f"DATE({column}) <= :p_to")
        params["p_to"] = end
    return (" AND " + " AND ".join(parts)) if parts else "", params


@company_bp.route("/")
@company_admin_required
def dashboard():
    company = current_user.company
    stats = {
        "customers": None,
        "vendors": None,
        "last_sync": company.last_sync_at if company else None,
        "has_synced_data": False,
    }
    cid = _company_id()
    if _table_exists("customers"):
        stats["has_synced_data"] = True
        # Prefer company_id filter when column exists; else show all (single-tenant sync)
        has_cid = _column_exists("customers", "company_id")
        if has_cid and cid:
            stats["customers"] = db.session.execute(
                text(
                    "SELECT COUNT(*) FROM customers WHERE company_id = :cid "
                    "AND (is_deleted = 0 OR is_deleted IS NULL)"
                ),
                {"cid": cid},
            ).scalar()
            if _table_exists("vendors"):
                stats["vendors"] = db.session.execute(
                    text(
                        "SELECT COUNT(*) FROM vendors WHERE company_id = :cid "
                        "AND (is_deleted = 0 OR is_deleted IS NULL)"
                    ),
                    {"cid": cid},
                ).scalar()
        else:
            stats["customers"] = db.session.execute(
                text(
                    "SELECT COUNT(*) FROM customers WHERE (is_deleted = 0 OR is_deleted IS NULL)"
                )
            ).scalar()
            if _table_exists("vendors"):
                stats["vendors"] = db.session.execute(
                    text(
                        "SELECT COUNT(*) FROM vendors WHERE (is_deleted = 0 OR is_deleted IS NULL)"
                    )
                ).scalar()

    return render_template(
        "company/dashboard.html",
        company=company,
        stats=stats,
        **_period_context("company.dashboard"),
    )


@company_bp.route("/customers")
@company_admin_required
def customers():
    rows = []
    error = None
    if not _table_exists("customers"):
        error = "No customer data synced yet. Use Sync in the offline app."
    else:
        has_cid = _column_exists("customers", "company_id")
        where = "WHERE (is_deleted = 0 OR is_deleted IS NULL)"
        params = {}
        if has_cid and _company_id():
            where += " AND company_id = :cid"
            params["cid"] = _company_id()
        rows = db.session.execute(
            text(
                f"""
                SELECT id, name, phone, address, opening_balance, balance
                FROM customers {where}
                ORDER BY name
                LIMIT 5000
                """
            ),
            params,
        ).mappings().all()
    return render_template(
        "company/customers.html",
        customers=rows,
        error=error,
        company=current_user.company,
        **_period_context("company.customers"),
    )


@company_bp.route("/customers/<int:customer_id>/ledger")
@company_admin_required
def customer_ledger(customer_id: int):
    if not _table_exists("ledger_entries"):
        flash("Ledger data not synced yet.", "warning")
        return redirect(url_for("company.customers"))
    customer = db.session.execute(
        text("SELECT id, name, phone, balance FROM customers WHERE id = :id"),
        {"id": customer_id},
    ).mappings().first()
    if not customer:
        flash("Customer not found.", "danger")
        return redirect(url_for("company.customers"))
    period_sql, params = _period_clause("entry_date")
    params["cid"] = customer_id
    entries = db.session.execute(
        text(
            f"""
            SELECT entry_date, notes, debit, credit, balance_after, entry_type
            FROM ledger_entries
            WHERE party_type = 'customer' AND party_id = :cid {period_sql}
            ORDER BY entry_date, id
            """
        ),
        params,
    ).mappings().all()
    return render_template(
        "company/customer_ledger.html",
        customer=customer,
        entries=entries,
        company=current_user.company,
        **_period_context("company.customer_ledger", customer_id=customer_id),
    )


@company_bp.route("/vendors")
@company_admin_required
def vendors():
    rows = []
    error = None
    if not _table_exists("vendors"):
        error = "No vendor data synced yet. Use Sync in the offline app."
    else:
        has_cid = _column_exists("vendors", "company_id")
        where = "WHERE (is_deleted = 0 OR is_deleted IS NULL)"
        params = {}
        if has_cid and _company_id():
            where += " AND company_id = :cid"
            params["cid"] = _company_id()
        rows = db.session.execute(
            text(
                f"""
                SELECT id, name, phone, address, opening_balance, balance
                FROM vendors {where}
                ORDER BY name
                LIMIT 5000
                """
            ),
            params,
        ).mappings().all()
    return render_template(
        "company/vendors.html",
        vendors=rows,
        error=error,
        company=current_user.company,
        **_period_context("company.vendors"),
    )


@company_bp.route("/vendors/<int:vendor_id>/ledger")
@company_admin_required
def vendor_ledger(vendor_id: int):
    if not _table_exists("ledger_entries"):
        flash("Ledger data not synced yet.", "warning")
        return redirect(url_for("company.vendors"))
    vendor = db.session.execute(
        text("SELECT id, name, phone, balance FROM vendors WHERE id = :id"),
        {"id": vendor_id},
    ).mappings().first()
    if not vendor:
        flash("Vendor not found.", "danger")
        return redirect(url_for("company.vendors"))
    period_sql, params = _period_clause("entry_date")
    params["vid"] = vendor_id
    entries = db.session.execute(
        text(
            f"""
            SELECT entry_date, notes, debit, credit, balance_after, entry_type
            FROM ledger_entries
            WHERE party_type = 'vendor' AND party_id = :vid {period_sql}
            ORDER BY entry_date, id
            """
        ),
        params,
    ).mappings().all()
    return render_template(
        "company/vendor_ledger.html",
        vendor=vendor,
        entries=entries,
        company=current_user.company,
        **_period_context("company.vendor_ledger", vendor_id=vendor_id),
    )

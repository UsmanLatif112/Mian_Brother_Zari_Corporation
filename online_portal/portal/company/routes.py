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


def _arg(name: str, default: str = "all") -> str:
    return (request.args.get(name) or default).strip().lower()


def _list_filter_args(**extra):
    """Preserve period + list filters across filter chip clear/submit links."""
    period, start, end = _period_bounds()
    args = {}
    if period and period != "all":
        args["period"] = period
        if period == "custom":
            if start:
                args["start_date"] = start
            if end:
                args["end_date"] = end
    for key, value in extra.items():
        if value not in (None, "", "all"):
            args[key] = value
    return args


def _customer_type_choices() -> list[tuple[str, str]]:
    choices: list[tuple[str, str]] = []
    if _table_exists("customer_types"):
        rows = db.session.execute(
            text(
                """
                SELECT code, name FROM customer_types
                WHERE (is_deleted = 0 OR is_deleted IS NULL)
                ORDER BY sort_order, name
                """
            )
        ).all()
        choices = [(str(code), str(name)) for code, name in rows if code]
    if not choices:
        choices = [
            ("good", "Good"),
            ("bad", "Bad"),
            ("1_year", "1 Year"),
            ("6_month", "6 Month"),
            ("late_pay", "Late Pay"),
        ]
    return choices


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


def _party_where(table: str) -> tuple[str, dict]:
    where = "WHERE (is_deleted = 0 OR is_deleted IS NULL)"
    params: dict = {}
    if _column_exists(table, "company_id") and _company_id():
        where += " AND company_id = :cid"
        params["cid"] = _company_id()
    return where, params


def _ledger_for_party_type(party_type: str):
    """All ledger rows for chart series (no period filter — buckets need history)."""
    if not _table_exists("ledger_entries"):
        return []
    return db.session.execute(
        text(
            """
            SELECT id, party_id, entry_date, entry_type, debit, credit, balance_after
            FROM ledger_entries
            WHERE party_type = :pt
            ORDER BY entry_date ASC, id ASC
            """
        ),
        {"pt": party_type},
    ).mappings().all()


@company_bp.route("/customers")
@company_admin_required
def customers():
    from portal.services.party_view import (
        customer_list_chart,
        filter_by_active,
        filter_by_customer_type,
        list_balance_stats,
    )

    rows = []
    error = None
    if not _table_exists("customers"):
        error = "No customer data synced yet. Use Sync in the offline app."
    else:
        where, params = _party_where("customers")
        cols = [
            "id",
            "name",
            "phone",
            "address",
            "opening_balance",
            "balance",
            "old_book_no",
            "joined_date",
            "customer_type",
            "is_active",
            "cnic",
            "credit_limit",
            "notes",
            "created_at",
        ]
        select_cols = ", ".join(c for c in cols if _column_exists("customers", c))
        rows = db.session.execute(
            text(f"SELECT {select_cols} FROM customers {where} ORDER BY name LIMIT 5000"),
            params,
        ).mappings().all()

    selected_type = _arg("type")
    selected_active = _arg("active")
    if selected_active not in {"all", "active", "inactive"}:
        selected_active = "all"

    rows = filter_by_customer_type(rows, selected_type)
    rows = filter_by_active(rows, selected_active)
    stats = list_balance_stats(rows)

    period, start, end = _period_bounds()
    chart = customer_list_chart(
        _ledger_for_party_type("customer"),
        period=period,
        start_date=start,
        end_date=end,
    )
    type_choices = _customer_type_choices()
    preserve = {
        "type": selected_type if selected_type != "all" else "",
        "active": selected_active if selected_active != "all" else "",
    }
    return render_template(
        "company/customers.html",
        customers=rows,
        error=error,
        company=current_user.company,
        total_credit=stats["total_credit"],
        total_advance=stats["total_advance"],
        list_chart=chart,
        total_records=len(rows),
        selected_type=selected_type,
        selected_active=selected_active,
        customer_type_choices=type_choices,
        filter_preserve=preserve,
        **_period_context(
            "company.customers",
            **{k: v for k, v in preserve.items() if v},
        ),
    )


@company_bp.route("/customers/<int:customer_id>/ledger")
@company_admin_required
def customer_ledger(customer_id: int):
    from portal.services.party_view import customer_account_pie, entry_type_label

    if not _table_exists("ledger_entries"):
        flash("Ledger data not synced yet.", "warning")
        return redirect(url_for("company.customers"))
    customer = db.session.execute(
        text(
            """
            SELECT id, name, phone, cnic, address, opening_balance, balance,
                   old_book_no, joined_date, customer_type, is_active,
                   credit_limit, notes, created_at
            FROM customers WHERE id = :id
            """
        ),
        {"id": customer_id},
    ).mappings().first()
    if not customer:
        flash("Customer not found.", "danger")
        return redirect(url_for("company.customers"))
    period, start, end = _period_bounds()
    period_sql, params = _period_clause("entry_date")
    params["cid"] = customer_id
    entries = db.session.execute(
        text(
            f"""
            SELECT id, entry_date, notes, debit, credit, balance_after, entry_type,
                   reference_type, reference_id
            FROM ledger_entries
            WHERE party_type = 'customer' AND party_id = :cid {period_sql}
            ORDER BY entry_date DESC, id DESC
            """
        ),
        params,
    ).mappings().all()
    display_balance = float(customer["balance"] or 0)
    account_pie = customer_account_pie(
        entries, display_balance, ranged=(period != "all")
    )
    return render_template(
        "company/customer_ledger.html",
        customer=customer,
        entries=entries,
        company=current_user.company,
        display_balance=display_balance,
        account_pie=account_pie,
        entry_type_label=entry_type_label,
        **_period_context("company.customer_ledger", customer_id=customer_id),
    )


@company_bp.route("/vendors")
@company_admin_required
def vendors():
    from portal.services.party_view import (
        filter_by_active,
        filter_by_vendor_balance,
        list_balance_stats,
        vendor_list_chart,
    )

    rows = []
    error = None
    if not _table_exists("vendors"):
        error = "No vendor data synced yet. Use Sync in the offline app."
    else:
        where, params = _party_where("vendors")
        cols = [
            "id",
            "name",
            "phone",
            "address",
            "opening_balance",
            "balance",
            "cnic",
            "is_active",
            "notes",
            "created_at",
        ]
        select_cols = ", ".join(c for c in cols if _column_exists("vendors", c))
        rows = db.session.execute(
            text(f"SELECT {select_cols} FROM vendors {where} ORDER BY name LIMIT 5000"),
            params,
        ).mappings().all()

    selected_status = _arg("status")
    selected_active = _arg("active")
    if selected_status not in {"all", "payable", "prepaid", "settled"}:
        selected_status = "all"
    if selected_active not in {"all", "active", "inactive"}:
        selected_active = "all"

    rows = filter_by_vendor_balance(rows, selected_status)
    rows = filter_by_active(rows, selected_active)
    stats = list_balance_stats(rows)

    period, start, end = _period_bounds()
    chart = vendor_list_chart(
        _ledger_for_party_type("vendor"),
        period=period,
        start_date=start,
        end_date=end,
    )
    # If ledger has no purchasable history, still show current payable/advance on the chart
    if not any(chart.get("total") or []) and not any(chart.get("paid") or []) and not any(
        chart.get("payable") or []
    ):
        chart = {
            "labels": ["Current"],
            "total": [0.0],
            "paid": [0.0],
            "payable": [round(stats["total_credit"], 2)],
            "count": [float(stats["count"])],
            "granularity": "day",
        }
        if stats["total_advance"]:
            chart["labels"] = ["Payable", "Advance"]
            chart["total"] = [0.0, 0.0]
            chart["paid"] = [0.0, 0.0]
            chart["payable"] = [round(stats["total_credit"], 2), round(stats["total_advance"], 2)]
            chart["count"] = [0.0, 0.0]

    preserve = {
        "status": selected_status if selected_status != "all" else "",
        "active": selected_active if selected_active != "all" else "",
    }
    return render_template(
        "company/vendors.html",
        vendors=rows,
        error=error,
        company=current_user.company,
        total_credit=stats["total_credit"],
        total_advance=stats["total_advance"],
        list_chart=chart,
        total_records=len(rows),
        selected_status=selected_status,
        selected_active=selected_active,
        filter_preserve=preserve,
        **_period_context(
            "company.vendors",
            **{k: v for k, v in preserve.items() if v},
        ),
    )


@company_bp.route("/vendors/<int:vendor_id>/ledger")
@company_admin_required
def vendor_ledger(vendor_id: int):
    from portal.services.party_view import entry_type_label, vendor_account_pie

    if not _table_exists("ledger_entries"):
        flash("Ledger data not synced yet.", "warning")
        return redirect(url_for("company.vendors"))
    vendor = db.session.execute(
        text(
            """
            SELECT id, name, phone, cnic, address, opening_balance, balance,
                   is_active, notes, created_at
            FROM vendors WHERE id = :id
            """
        ),
        {"id": vendor_id},
    ).mappings().first()
    if not vendor:
        flash("Vendor not found.", "danger")
        return redirect(url_for("company.vendors"))
    period, start, end = _period_bounds()
    period_sql, params = _period_clause("entry_date")
    params["vid"] = vendor_id
    entries = db.session.execute(
        text(
            f"""
            SELECT id, entry_date, notes, debit, credit, balance_after, entry_type,
                   reference_type, reference_id
            FROM ledger_entries
            WHERE party_type = 'vendor' AND party_id = :vid {period_sql}
            ORDER BY entry_date DESC, id DESC
            """
        ),
        params,
    ).mappings().all()
    display_balance = float(vendor["balance"] or 0)
    account_pie = vendor_account_pie(
        entries, display_balance, ranged=(period != "all")
    )
    # If period filter emptied the pie, rebuild from all-time ledger so detail graph still shows
    if not account_pie["pie"]["rows"]:
        all_entries = db.session.execute(
            text(
                """
                SELECT id, entry_date, notes, debit, credit, balance_after, entry_type,
                       reference_type, reference_id
                FROM ledger_entries
                WHERE party_type = 'vendor' AND party_id = :vid
                ORDER BY entry_date DESC, id DESC
                """
            ),
            {"vid": vendor_id},
        ).mappings().all()
        account_pie = vendor_account_pie(all_entries, display_balance, ranged=False)
    return render_template(
        "company/vendor_ledger.html",
        vendor=vendor,
        entries=entries,
        company=current_user.company,
        display_balance=display_balance,
        account_pie=account_pie,
        entry_type_label=entry_type_label,
        **_period_context("company.vendor_ledger", vendor_id=vendor_id),
    )

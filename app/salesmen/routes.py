from datetime import date as date_cls
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.forms import SalesmanForm
from app.models import LedgerEntry, Sale, Salesman
from app.services.audit_service import log_audit
from app.services.ledger_service import delete_ledger_entry, post_ledger_entry
from app.utils.decorators import permission_required
from app.utils.uploads import delete_image, save_image
from app.utils.working_date import get_working_date

salesmen_bp = Blueprint("salesmen", __name__)


def _apply_salesman_photo(salesman):
    if request.form.get("clear_photo") == "1":
        delete_image(salesman.photo)
        salesman.photo = None
        return
    try:
        path = save_image(request.files.get("photo"), "salesmen")
    except ValueError:
        raise
    if path:
        delete_image(salesman.photo)
        salesman.photo = path


def _parse_date(value):
    if not value:
        return None
    try:
        return date_cls.fromisoformat(value)
    except ValueError:
        return None


def _sales_totals_by_salesman(salesman_ids, range_start=None, range_end=None):
    """Map salesman_id → total grand_total of attributed sales (optionally by sale_date)."""
    if not salesman_ids:
        return {}
    q = (
        db.session.query(Sale.salesman_id, func.coalesce(func.sum(Sale.grand_total), 0))
        .filter(Sale.salesman_id.in_(salesman_ids))
    )
    if range_start:
        q = q.filter(Sale.sale_date >= range_start)
    if range_end:
        q = q.filter(Sale.sale_date <= range_end)
    rows = q.group_by(Sale.salesman_id).all()
    return {int(sid): Decimal(str(total or 0)) for sid, total in rows}


def _salesman_page(form=None, open_modal=False):
    from datetime import datetime, time

    from app.services.dashboard_service import _range_for_filter

    period = request.args.get("period", "all")
    period_start = _parse_date(request.args.get("start_date"))
    period_end = _parse_date(request.args.get("end_date"))
    range_start, range_end = _range_for_filter(period, period_start, period_end)

    query = Salesman.query.filter_by(is_deleted=False)
    if range_start and range_end:
        start_dt = datetime.combine(range_start, time.min)
        end_dt = datetime.combine(range_end, time.max)
        query = query.filter(Salesman.created_at >= start_dt, Salesman.created_at <= end_dt)

    salesmen = query.order_by(Salesman.name).all()

    from app.services.ledger_service import sum_party_balances_as_of

    # Sales totals for the period across all active salesmen (not only those created in range)
    all_ids = [s.id for s in Salesman.query.filter_by(is_deleted=False).all()]
    period_totals = _sales_totals_by_salesman(
        all_ids, range_start=range_start, range_end=range_end
    )
    list_totals = _sales_totals_by_salesman(
        [s.id for s in salesmen], range_start=range_start, range_end=range_end
    )
    for s in salesmen:
        s.total_sales = list_totals.get(s.id, Decimal("0"))

    total_sales_all = sum((period_totals.get(sid, Decimal("0")) for sid in all_ids), Decimal("0"))
    if range_end:
        total_credit, _ = sum_party_balances_as_of("salesman", range_end)
        period_as_of = range_end
    else:
        total_credit = (
            db.session.query(func.coalesce(func.sum(Salesman.balance), 0))
            .filter(Salesman.is_deleted.is_(False), Salesman.balance > 0)
            .scalar()
            or Decimal("0")
        )
        period_as_of = None

    return render_template(
        "salesmen/index.html",
        salesmen=salesmen,
        form=form or SalesmanForm(),
        open_modal=open_modal or request.args.get("open_modal") == "1",
        today=get_working_date().isoformat(),
        total_sales_all=total_sales_all,
        total_credit=total_credit,
        period_as_of=period_as_of,
        selected_period=period,
        start_date=period_start.isoformat() if period_start else "",
        end_date=period_end.isoformat() if period_end else "",
    )


@salesmen_bp.route("/")
@login_required
@permission_required("salesmen.view")
def index():
    return _salesman_page()


@salesmen_bp.route("/create", methods=["GET", "POST"])
@login_required
@permission_required("salesmen.*")
def create():
    if request.method == "GET":
        return redirect(url_for("salesmen.index", open_modal=1))
    form = SalesmanForm()
    if form.validate_on_submit():
        try:
            opening_raw = form.opening_balance.data
            opening = Decimal("0") if opening_raw in (None, "") else Decimal(str(opening_raw))
            salesman = Salesman(
                name=form.name.data.strip(),
                phone=(form.phone.data or "").strip() or None,
                company=(form.company.data or "").strip() or None,
                address=(form.address.data or "").strip() or None,
                opening_balance=opening,
                balance=opening,
                notes=(form.notes.data or "").strip() or None,
            )
            _apply_salesman_photo(salesman)
            db.session.add(salesman)
            db.session.flush()
            if opening:
                opening_d = Decimal(str(opening))
                debit = opening_d if opening_d > 0 else Decimal("0")
                credit = abs(opening_d) if opening_d < 0 else Decimal("0")
                post_ledger_entry(
                    "salesman",
                    salesman.id,
                    "opening",
                    debit=debit,
                    credit=credit,
                    entry_date=get_working_date(),
                    notes="Opening balance",
                )
            log_audit("create", "salesman", None, salesman.name)
            db.session.commit()
            flash("Salesman created.", "success")
            return redirect(url_for("salesmen.index"))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            return _salesman_page(form=form, open_modal=True)
    return _salesman_page(form=form, open_modal=True)


@salesmen_bp.route("/<int:salesman_id>/edit", methods=["POST"])
@login_required
@permission_required("salesmen.*")
def edit(salesman_id):
    salesman = db.session.get(Salesman, salesman_id)
    if not salesman or salesman.is_deleted:
        flash("Salesman not found.", "danger")
        return redirect(url_for("salesmen.index"))
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Name is required.", "danger")
        return redirect(url_for("salesmen.index"))
    try:
        salesman.name = name
        salesman.phone = (request.form.get("phone") or "").strip() or None
        salesman.company = (request.form.get("company") or "").strip() or None
        salesman.address = (request.form.get("address") or "").strip() or None
        salesman.notes = (request.form.get("notes") or "").strip() or None
        opening = request.form.get("opening_balance")
        salesman.opening_balance = (
            Decimal("0") if opening in (None, "") else Decimal(str(opening))
        )
        _apply_salesman_photo(salesman)
        log_audit("update", "salesman", salesman.id, salesman.name)
        db.session.commit()
        flash("Salesman updated.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    if request.form.get("return_detail"):
        return redirect(url_for("salesmen.detail", salesman_id=salesman.id))
    return redirect(url_for("salesmen.index"))


@salesmen_bp.route("/<int:salesman_id>/delete", methods=["POST"])
@login_required
@permission_required("salesmen.*")
def delete(salesman_id):
    salesman = db.session.get(Salesman, salesman_id)
    if not salesman or salesman.is_deleted:
        flash("Salesman not found.", "danger")
        return redirect(url_for("salesmen.index"))
    salesman.soft_delete()
    log_audit("delete", "salesman", salesman.id, salesman.name)
    db.session.commit()
    flash("Salesman deleted.", "success")
    return redirect(url_for("salesmen.index"))


@salesmen_bp.route("/ledger/<int:entry_id>/delete", methods=["POST"])
@login_required
@permission_required("salesmen.*")
def delete_ledger(entry_id):
    """Remove salesman attribution only — does not delete/void the sale."""
    entry = db.session.get(LedgerEntry, entry_id)
    if not entry or entry.party_type != "salesman":
        flash("Ledger entry not found.", "danger")
        return redirect(url_for("salesmen.index"))
    party_id = entry.party_id
    try:
        ref_type = (entry.reference_type or "").strip().lower()
        ref_id = entry.reference_id
        if ref_type == "sale" and ref_id:
            sale = db.session.get(Sale, int(ref_id))
            if sale and sale.salesman_id == party_id:
                sale.salesman_id = None
        delete_ledger_entry(entry_id)
        db.session.commit()
        flash("Unlinked from salesman. Sale was not deleted.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("salesmen.detail", salesman_id=party_id))


@salesmen_bp.route("/<int:salesman_id>")
@login_required
@permission_required("salesmen.view")
def detail(salesman_id):
    from datetime import datetime, time

    from app.services.dashboard_service import _range_for_filter

    salesman = db.session.get(Salesman, salesman_id)
    if not salesman or salesman.is_deleted:
        flash("Salesman not found.", "danger")
        return redirect(url_for("salesmen.index"))

    period = request.args.get("period", "all")
    period_start = _parse_date(request.args.get("start_date"))
    period_end = _parse_date(request.args.get("end_date"))
    range_start, range_end = _range_for_filter(period, period_start, period_end)

    ledger_q = LedgerEntry.query.filter_by(party_type="salesman", party_id=salesman.id)
    if range_start and range_end:
        ledger_q = ledger_q.filter(
            LedgerEntry.entry_date >= range_start,
            LedgerEntry.entry_date <= range_end,
        )
    ledger = ledger_q.order_by(LedgerEntry.entry_date.desc(), LedgerEntry.id.desc()).all()

    # Attach invoice + particulars for sale rows
    from app.services.sale_service import _sale_particulars_notes

    sale_ids = [
        int(e.reference_id)
        for e in ledger
        if (e.reference_type or "").lower() == "sale" and e.reference_id
    ]
    sales_map = {}
    notes_dirty = False
    if sale_ids:
        from app.models import SaleItem

        sales = (
            Sale.query.options(joinedload(Sale.items).joinedload(SaleItem.product))
            .filter(Sale.id.in_(sale_ids))
            .all()
        )
        for sale in sales:
            from app.services.journal_service import items_particulars

            sales_map[sale.id] = {
                "invoice_no": sale.invoice_no,
                "particulars": items_particulars(
                    sale.items, fallback=sale.notes or "—", kind="sale"
                ),
                "notes": _sale_particulars_notes(sale),
            }

    for entry in ledger:
        info = sales_map.get(int(entry.reference_id)) if entry.reference_id else None
        entry.invoice_no = (info or {}).get("invoice_no") or "—"
        if (entry.entry_type or "").lower() == "sale" and info:
            entry.particulars = info.get("particulars") or entry.notes or "—"
            desired = info.get("notes")
            if desired and desired != (entry.notes or ""):
                current = (entry.notes or "").strip()
                inv = (info.get("invoice_no") or "").strip()
                bare = (
                    not current
                    or current == inv
                    or current == f"Sale {inv}"
                    or (current.lower().startswith("sale ") and " / " not in current)
                )
                if bare:
                    entry.notes = desired
                    notes_dirty = True
        else:
            entry.particulars = entry.notes or "—"

    if notes_dirty:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

    totals = _sales_totals_by_salesman(
        [salesman.id], range_start=range_start, range_end=range_end
    )
    total_sales = totals.get(salesman.id, Decimal("0"))

    from app.services.ledger_service import party_balance_as_of

    if range_end:
        display_balance = party_balance_as_of("salesman", salesman.id, range_end)
        balance_as_of = range_end
    else:
        display_balance = Decimal(str(salesman.balance or 0))
        balance_as_of = None

    return render_template(
        "salesmen/detail.html",
        salesman=salesman,
        ledger=ledger,
        total_sales=total_sales,
        display_balance=display_balance,
        balance_as_of=balance_as_of,
        today=get_working_date().isoformat(),
        selected_period=period,
        start_date=period_start.isoformat() if period_start else "",
        end_date=period_end.isoformat() if period_end else "",
    )

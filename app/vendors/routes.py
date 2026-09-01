from datetime import date as date_cls
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from sqlalchemy.orm import joinedload

from app.extensions import db
from app.forms import VendorForm
from app.models import LedgerEntry, Vendor, VendorPayment, VendorPhoto
from app.services.audit_service import log_audit
from app.services.vendor_payment_service import (
    PAYMENT_TYPES,
    delete_vendor_payment,
    record_vendor_payment,
)
from app.services.ledger_service import delete_ledger_entry_cascading
from app.utils.decorators import permission_required
from app.utils.party_filters import apply_party_active_filter, parse_party_active
from app.utils.uploads import accept_uploaded_path, delete_image, save_image
from app.utils.working_date import get_working_date

vendors_bp = Blueprint("vendors", __name__)


def _sync_vendor_primary_photo(vendor):
    if vendor.photos:
        vendor.photo = vendor.photos[0].path
    else:
        vendor.photo = None


def _apply_vendor_photos(vendor):
    remove_ids = request.form.getlist("remove_photo_id")
    for raw_id in remove_ids:
        try:
            photo_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        photo = db.session.get(VendorPhoto, photo_id)
        if not photo or photo.vendor_id != vendor.id:
            continue
        delete_image(photo.path)
        db.session.delete(photo)

    for path in request.form.getlist("new_photo_paths"):
        clean = accept_uploaded_path(path, "vendors")
        if not clean:
            continue
        sort_order = len(vendor.photos or [])
        db.session.add(
            VendorPhoto(vendor_id=vendor.id, path=clean, sort_order=sort_order)
        )

    files = request.files.getlist("customer_photos")
    single = request.files.get("photo")
    if single and getattr(single, "filename", None):
        files = list(files) + [single]

    for f in files:
        if not f or not getattr(f, "filename", None):
            continue
        try:
            path = save_image(f, "vendors")
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        if path:
            sort_order = len(vendor.photos or [])
            db.session.add(
                VendorPhoto(vendor_id=vendor.id, path=path, sort_order=sort_order)
            )

    _sync_vendor_primary_photo(vendor)


def _parse_date(value):
    if not value:
        return None
    try:
        return date_cls.fromisoformat(value)
    except ValueError:
        return None


def _vendor_page(form=None, open_modal=False):
    from datetime import datetime, time

    from app.services.dashboard_service import _range_for_filter

    period = request.args.get("period", "all")
    period_start = _parse_date(request.args.get("start_date"))
    period_end = _parse_date(request.args.get("end_date"))
    range_start, range_end = _range_for_filter(period, period_start, period_end)
    status = (request.args.get("status") or "all").strip().lower()
    if status not in ("all", "payable", "prepaid", "settled"):
        status = "all"
    active_filter = parse_party_active(request.args.get("active"), default="all")

    query = Vendor.query.filter_by(is_deleted=False)
    query = apply_party_active_filter(query, Vendor, active_filter)
    if range_start and range_end:
        start_dt = datetime.combine(range_start, time.min)
        end_dt = datetime.combine(range_end, time.max)
        query = query.filter(Vendor.created_at >= start_dt, Vendor.created_at <= end_dt)
    if status == "payable":
        query = query.filter(Vendor.balance > 0)
    elif status == "prepaid":
        query = query.filter(Vendor.balance < 0)
    elif status == "settled":
        query = query.filter(Vendor.balance == 0)

    vendors = query.options(joinedload(Vendor.photos)).order_by(Vendor.name).all()

    from app.services.ledger_service import sum_party_balances_as_of

    if range_end:
        total_payable, total_prepaid = sum_party_balances_as_of("vendor", range_end)
        period_as_of = range_end
    else:
        total_payable = (
            db.session.query(func.coalesce(func.sum(Vendor.balance), 0))
            .filter(Vendor.is_deleted.is_(False), Vendor.balance > 0)
            .scalar()
            or Decimal("0")
        )
        total_prepaid = (
            db.session.query(func.coalesce(func.sum(-Vendor.balance), 0))
            .filter(Vendor.is_deleted.is_(False), Vendor.balance < 0)
            .scalar()
            or Decimal("0")
        )
        period_as_of = None

    from app.services.vendor_list_analytics_service import vendor_list_chart_metrics

    vendor_chart = vendor_list_chart_metrics(
        period=period,
        start_date=period_start,
        end_date=period_end,
    )["chart"]

    return render_template(
        "vendors/index.html",
        vendors=vendors,
        form=form or VendorForm(),
        open_modal=open_modal or request.args.get("open_modal") == "1",
        today=get_working_date().isoformat(),
        payment_types=PAYMENT_TYPES,
        total_payable=total_payable,
        total_prepaid=total_prepaid,
        period_as_of=period_as_of,
        vendor_chart=vendor_chart,
        selected_period=period,
        start_date=period_start.isoformat() if period_start else "",
        end_date=period_end.isoformat() if period_end else "",
        selected_status=status,
        selected_active=active_filter,
    )


@vendors_bp.route("/")
@login_required
@permission_required("vendors.view")
def index():
    return _vendor_page()


@vendors_bp.route("/create", methods=["GET", "POST"])
@login_required
@permission_required("vendors.*")
def create():
    if request.method == "GET":
        return redirect(url_for("vendors.index", open_modal=1))
    form = VendorForm()
    if form.validate_on_submit():
        try:
            opening_raw = form.opening_balance.data
            opening = Decimal("0") if opening_raw in (None, "") else Decimal(str(opening_raw))
            vendor = Vendor(
                name=form.name.data,
                phone=form.phone.data,
                cnic=(form.cnic.data or "").strip() or None,
                address=form.address.data,
                opening_balance=opening,
                balance=opening,
                notes=form.notes.data,
                is_active=True,
            )
            db.session.add(vendor)
            db.session.flush()
            _apply_vendor_photos(vendor)
            if opening:
                from app.services.ledger_service import post_ledger_entry

                opening_d = Decimal(str(opening))
                # Vendor balance > 0 means we owe them (debit payable)
                debit = opening_d if opening_d > 0 else Decimal("0")
                credit = abs(opening_d) if opening_d < 0 else Decimal("0")
                post_ledger_entry(
                    "vendor",
                    vendor.id,
                    "opening",
                    debit=debit,
                    credit=credit,
                    entry_date=get_working_date(),
                    notes="Opening balance",
                )
            log_audit("create", "vendor", None, vendor.name)
            db.session.commit()
            flash("Vendor created.", "success")
            return redirect(url_for("vendors.index"))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            return _vendor_page(form=form, open_modal=True)
    return _vendor_page(form=form, open_modal=True)


@vendors_bp.route("/<int:vendor_id>/edit", methods=["POST"])
@login_required
@permission_required("vendors.*")
def edit(vendor_id):
    vendor = db.session.get(Vendor, vendor_id)
    if not vendor or vendor.is_deleted:
        flash("Vendor not found.", "danger")
        return redirect(url_for("vendors.index"))
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Name is required.", "danger")
        return redirect(url_for("vendors.index"))
    try:
        vendor.name = name
        vendor.phone = (request.form.get("phone") or "").strip() or None
        vendor.cnic = (request.form.get("cnic") or "").strip() or None
        vendor.address = (request.form.get("address") or "").strip() or None
        vendor.notes = (request.form.get("notes") or "").strip() or None
        opening = request.form.get("opening_balance")
        vendor.opening_balance = (
            Decimal("0") if opening in (None, "") else Decimal(str(opening))
        )
        vendor.is_active = request.form.get("is_active", "1") == "1"
        _apply_vendor_photos(vendor)
        log_audit("update", "vendor", vendor.id, vendor.name)
        db.session.commit()
        flash("Vendor updated.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    if request.form.get("return_detail"):
        return redirect(url_for("vendors.detail", vendor_id=vendor.id))
    return redirect(url_for("vendors.index"))


@vendors_bp.route("/payment", methods=["POST"])
@login_required
@permission_required("vendors.*")
def payment():
    vendor_id = request.form.get("vendor_id")
    payment_type = request.form.get("payment_type")
    amount = request.form.get("amount")
    payment_date = request.form.get("payment_date") or get_working_date().isoformat()
    remarks = request.form.get("remarks")

    try:
        payment = record_vendor_payment(
            vendor_id=int(vendor_id),
            payment_type=payment_type,
            amount=amount,
            payment_date=payment_date,
            remarks=remarks,
            user_id=current_user.id,
        )
        log_audit("create", "vendor_payment", payment.id, payment_type)
        db.session.commit()
        flash(
            f"Payment recorded ({PAYMENT_TYPES.get(payment_type, payment_type)}).",
            "success",
        )
        if request.form.get("return_detail"):
            return redirect(url_for("vendors.detail", vendor_id=payment.vendor_id))
        return redirect(url_for("vendors.index"))
    except (ValueError, TypeError) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        if request.form.get("return_detail") and vendor_id:
            try:
                return redirect(url_for("vendors.detail", vendor_id=int(vendor_id)))
            except (TypeError, ValueError):
                pass
        return redirect(url_for("vendors.index"))


@vendors_bp.route("/payments/<int:payment_id>/delete", methods=["POST"])
@login_required
@permission_required("vendors.*")
def delete_payment(payment_id):
    try:
        vendor_id = delete_vendor_payment(payment_id, current_user.id)
        log_audit("delete", "vendor_payment", payment_id)
        db.session.commit()
        flash("Payment deleted and reversed.", "success")
        return redirect(url_for("vendors.detail", vendor_id=vendor_id))
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("vendors.index"))


@vendors_bp.route("/ledger/<int:entry_id>/delete", methods=["POST"])
@login_required
@permission_required("vendors.*")
def delete_ledger(entry_id):
    try:
        party_type, party_id, source = delete_ledger_entry_cascading(
            entry_id, current_user.id
        )
        log_audit("delete", "ledger_entry", entry_id, source)
        db.session.commit()
        messages = {
            "sale": "Sale deleted. Stock, cash, and ledger reversed.",
            "customer_receiving": "Customer payment deleted and reversed.",
            "purchase": "Purchase reversed. Stock, purchasing totals, and payable updated.",
            "vendor_payment": "Payment deleted. Cash and vendor balance reversed.",
            "ledger": "Ledger entry deleted. Balance recalculated.",
        }
        flash(messages.get(source, "Entry deleted and balances recalculated."), "success")
        if party_type == "vendor":
            return redirect(url_for("vendors.detail", vendor_id=party_id))
        if party_type == "customer":
            return redirect(url_for("customers.detail", customer_id=party_id))
        return redirect(url_for("vendors.index"))
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("vendors.index"))


@vendors_bp.route("/<int:vendor_id>")
@login_required
@permission_required("vendors.view")
def detail(vendor_id):
    from app.services.dashboard_service import _range_for_filter

    vendor = (
        Vendor.query.options(joinedload(Vendor.photos))
        .filter_by(id=vendor_id, is_deleted=False)
        .first()
    )
    if not vendor:
        flash("Vendor not found.", "danger")
        return redirect(url_for("vendors.index"))

    period = request.args.get("period", "all")
    period_start = _parse_date(request.args.get("start_date"))
    period_end = _parse_date(request.args.get("end_date"))
    range_start, range_end = _range_for_filter(period, period_start, period_end)

    ledger_q = LedgerEntry.query.filter_by(party_type="vendor", party_id=vendor_id)
    pay_q = VendorPayment.query.filter_by(vendor_id=vendor_id)
    if range_start:
        ledger_q = ledger_q.filter(LedgerEntry.entry_date >= range_start)
        pay_q = pay_q.filter(VendorPayment.payment_date >= range_start)
    if range_end:
        ledger_q = ledger_q.filter(LedgerEntry.entry_date <= range_end)
        pay_q = pay_q.filter(VendorPayment.payment_date <= range_end)

    ledger = ledger_q.order_by(LedgerEntry.entry_date, LedgerEntry.id).all()

    # Show what was purchased on purchase ledger rows
    from app.models import Purchase
    from app.services.purchase_service import purchase_items_summary

    purchase_ids = [
        int(l.reference_id)
        for l in ledger
        if (l.reference_type or "") == "purchase" and l.reference_id
    ]
    purchases_by_id = {}
    if purchase_ids:
        for p in Purchase.query.filter(Purchase.id.in_(set(purchase_ids))).all():
            purchases_by_id[p.id] = p
    notes_dirty = False
    for entry in ledger:
        if (entry.reference_type or "") == "purchase" and entry.reference_id:
            purchase = purchases_by_id.get(int(entry.reference_id))
            summary = purchase_items_summary(purchase) if purchase else ""
            if summary and summary != (entry.notes or ""):
                entry.notes = summary
                notes_dirty = True
    if notes_dirty:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

    payments = pay_q.order_by(VendorPayment.payment_date.desc()).all()

    from app.services.ledger_service import party_balance_as_of
    from app.services.party_analytics_service import vendor_account_pie

    if range_end:
        display_balance = party_balance_as_of("vendor", vendor_id, range_end)
        balance_as_of = range_end
    else:
        display_balance = Decimal(str(vendor.balance or 0))
        balance_as_of = None

    account_pie = vendor_account_pie(
        vendor_id,
        range_start=range_start,
        range_end=range_end,
        display_balance=display_balance,
    )

    return render_template(
        "vendors/detail.html",
        vendor=vendor,
        ledger=ledger,
        payments=payments,
        display_balance=display_balance,
        balance_as_of=balance_as_of,
        account_pie=account_pie,
        today=get_working_date().isoformat(),
        payment_types=PAYMENT_TYPES,
        selected_period=period,
        start_date=period_start.isoformat() if period_start else "",
        end_date=period_end.isoformat() if period_end else "",
    )

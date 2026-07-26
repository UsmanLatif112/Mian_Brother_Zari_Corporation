from datetime import date as date_cls
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from app.extensions import db
from app.forms import VendorForm
from app.models import LedgerEntry, Vendor, VendorPayment
from app.services.audit_service import log_audit
from app.services.vendor_payment_service import PAYMENT_TYPES, record_vendor_payment
from app.utils.decorators import permission_required
from app.utils.uploads import delete_image, save_image

vendors_bp = Blueprint("vendors", __name__)


def _apply_vendor_photo(vendor):
    if request.form.get("clear_photo") == "1":
        delete_image(vendor.photo)
        vendor.photo = None
        return
    try:
        path = save_image(request.files.get("photo"), "vendors")
    except ValueError:
        raise
    if path:
        delete_image(vendor.photo)
        vendor.photo = path


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

    query = Vendor.query.filter_by(is_deleted=False)
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

    vendors = query.order_by(Vendor.name).all()

    base = [Vendor.is_deleted.is_(False)]
    if range_start and range_end:
        start_dt = datetime.combine(range_start, time.min)
        end_dt = datetime.combine(range_end, time.max)
        base.extend([Vendor.created_at >= start_dt, Vendor.created_at <= end_dt])

    total_payable = (
        db.session.query(func.coalesce(func.sum(Vendor.balance), 0))
        .filter(*base, Vendor.balance > 0)
        .scalar()
        or Decimal("0")
    )
    total_prepaid = (
        db.session.query(func.coalesce(func.sum(-Vendor.balance), 0))
        .filter(*base, Vendor.balance < 0)
        .scalar()
        or Decimal("0")
    )
    return render_template(
        "vendors/index.html",
        vendors=vendors,
        form=form or VendorForm(),
        open_modal=open_modal or request.args.get("open_modal") == "1",
        today=date_cls.today().isoformat(),
        payment_types=PAYMENT_TYPES,
        total_payable=total_payable,
        total_prepaid=total_prepaid,
        selected_period=period,
        start_date=period_start.isoformat() if period_start else "",
        end_date=period_end.isoformat() if period_end else "",
        selected_status=status,
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
                address=form.address.data,
                opening_balance=opening,
                balance=opening,
                notes=form.notes.data,
            )
            _apply_vendor_photo(vendor)
            db.session.add(vendor)
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
        vendor.address = (request.form.get("address") or "").strip() or None
        vendor.notes = (request.form.get("notes") or "").strip() or None
        opening = request.form.get("opening_balance")
        vendor.opening_balance = (
            Decimal("0") if opening in (None, "") else Decimal(str(opening))
        )
        _apply_vendor_photo(vendor)
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
    payment_date = request.form.get("payment_date") or date_cls.today().isoformat()
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


@vendors_bp.route("/<int:vendor_id>")
@login_required
@permission_required("vendors.view")
def detail(vendor_id):
    from app.services.dashboard_service import _range_for_filter

    vendor = db.session.get(Vendor, vendor_id)
    if not vendor or vendor.is_deleted:
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
    for entry in ledger:
        if (entry.reference_type or "") == "purchase" and entry.reference_id:
            purchase = purchases_by_id.get(int(entry.reference_id))
            summary = purchase_items_summary(purchase) if purchase else ""
            if summary:
                entry.notes = summary

    payments = pay_q.order_by(VendorPayment.payment_date.desc()).all()
    return render_template(
        "vendors/detail.html",
        vendor=vendor,
        ledger=ledger,
        payments=payments,
        today=date_cls.today().isoformat(),
        payment_types=PAYMENT_TYPES,
        selected_period=period,
        start_date=period_start.isoformat() if period_start else "",
        end_date=period_end.isoformat() if period_end else "",
    )

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

vendors_bp = Blueprint("vendors", __name__)


def _vendor_page(form=None, open_modal=False):
    vendors = Vendor.query.filter_by(is_deleted=False).order_by(Vendor.name).all()
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
    return render_template(
        "vendors/index.html",
        vendors=vendors,
        form=form or VendorForm(),
        open_modal=open_modal or request.args.get("open_modal") == "1",
        today=date_cls.today().isoformat(),
        payment_types=PAYMENT_TYPES,
        total_payable=total_payable,
        total_prepaid=total_prepaid,
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
        vendor = Vendor(
            name=form.name.data,
            phone=form.phone.data,
            address=form.address.data,
            opening_balance=form.opening_balance.data or 0,
            balance=form.opening_balance.data or 0,
            notes=form.notes.data,
        )
        db.session.add(vendor)
        log_audit("create", "vendor", None, vendor.name)
        db.session.commit()
        flash("Vendor created.", "success")
        return redirect(url_for("vendors.index"))
    return _vendor_page(form=form, open_modal=True)


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
    vendor = db.session.get(Vendor, vendor_id)
    if not vendor or vendor.is_deleted:
        flash("Vendor not found.", "danger")
        return redirect(url_for("vendors.index"))
    ledger = (
        LedgerEntry.query.filter_by(party_type="vendor", party_id=vendor_id)
        .order_by(LedgerEntry.entry_date, LedgerEntry.id)
        .all()
    )
    payments = (
        VendorPayment.query.filter_by(vendor_id=vendor_id)
        .order_by(VendorPayment.payment_date.desc())
        .all()
    )
    return render_template(
        "vendors/detail.html",
        vendor=vendor,
        ledger=ledger,
        payments=payments,
        today=date_cls.today().isoformat(),
        payment_types=PAYMENT_TYPES,
    )

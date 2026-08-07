from datetime import date as date_cls
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from app.extensions import db
from app.forms import CustomerForm
from app.models import Customer, CustomerReceiving, LedgerEntry
from app.services.audit_service import log_audit
from app.services.customer_payment_service import (
    PAYMENT_TYPES,
    delete_customer_payment,
    record_customer_payment,
)
from app.services.ledger_service import (
    delete_ledger_entry_cascading,
    sync_party_opening_entry,
    update_ledger_entry,
)
from app.utils.decorators import permission_required
from app.utils.uploads import delete_image, save_image
from app.utils.working_date import get_working_date

customers_bp = Blueprint("customers", __name__)


def _apply_customer_photo(customer):
    """Handle optional photo upload / clear from multipart form."""
    if request.form.get("clear_photo") == "1":
        delete_image(customer.photo)
        customer.photo = None
        return
    try:
        path = save_image(request.files.get("photo"), "customers")
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    if path:
        delete_image(customer.photo)
        customer.photo = path


def _opening_notes(customer):
    return "Old book balance" + (
        f" ({customer.old_book_no})" if customer.old_book_no else ""
    )


def _ensure_customer_opening(customer):
    """
    Ensure ledger has an Opening row matching opening_balance (date = joined_date).
    Rebuilds Customer.balance = old + later in/out. Returns True if anything changed.
    """
    opening = Decimal(str(customer.opening_balance or 0))
    rows = (
        LedgerEntry.query.filter_by(
            party_type="customer", party_id=customer.id, entry_type="opening"
        )
        .order_by(LedgerEntry.id.asc())
        .all()
    )
    primary = rows[0] if rows else None
    led = (
        Decimal(str(primary.debit or 0)) - Decimal(str(primary.credit or 0))
        if primary
        else Decimal("0")
    )
    date_mismatch = bool(
        primary
        and customer.joined_date
        and primary.entry_date != customer.joined_date
    )
    if opening == led and len(rows) <= 1 and not date_mismatch:
        return False
    sync_party_opening_entry(
        "customer",
        customer.id,
        opening,
        entry_date=customer.joined_date or get_working_date(),
        notes=_opening_notes(customer),
    )
    return True


def _heal_customers_opening(customers):
    changed = False
    for customer in customers:
        if _ensure_customer_opening(customer):
            changed = True
    if changed:
        db.session.commit()


def _parse_date(value):
    if not value:
        return None
    try:
        return date_cls.fromisoformat(value)
    except ValueError:
        return None


def _customer_page(form=None, open_modal=False):
    from app.services.dashboard_service import _range_for_filter

    form = form or CustomerForm()
    if not form.joined_date.data:
        form.joined_date.data = get_working_date()

    period = request.args.get("period", "all")
    period_start = _parse_date(request.args.get("start_date"))
    period_end = _parse_date(request.args.get("end_date"))
    range_start, range_end = _range_for_filter(period, period_start, period_end)
    ctype = (request.args.get("type") or "all").strip().lower()

    query = Customer.query.filter_by(is_deleted=False)
    if range_start:
        query = query.filter(Customer.joined_date >= range_start)
    if range_end:
        query = query.filter(Customer.joined_date <= range_end)
    if ctype and ctype != "all":
        query = query.filter(Customer.customer_type == ctype)

    customers = query.order_by(Customer.name).all()
    _heal_customers_opening(customers)

    base = [Customer.is_deleted.is_(False)]
    if range_start:
        base.append(Customer.joined_date >= range_start)
    if range_end:
        base.append(Customer.joined_date <= range_end)
    if ctype and ctype != "all":
        base.append(Customer.customer_type == ctype)
    total_credit = (
        db.session.query(func.coalesce(func.sum(Customer.balance), 0))
        .filter(*base, Customer.balance > 0)
        .scalar()
        or Decimal("0")
    )
    total_advance = (
        db.session.query(func.coalesce(func.sum(-Customer.balance), 0))
        .filter(*base, Customer.balance < 0)
        .scalar()
        or Decimal("0")
    )

    if not open_modal:
        open_modal = bool(session.pop("open_customer_modal", False))

    return render_template(
        "customers/index.html",
        customers=customers,
        form=form,
        total_credit=total_credit,
        total_advance=total_advance,
        open_modal=open_modal,
        today=get_working_date().isoformat(),
        payment_types=PAYMENT_TYPES,
        selected_period=period,
        start_date=period_start.isoformat() if period_start else "",
        end_date=period_end.isoformat() if period_end else "",
        selected_type=ctype if ctype != "all" else "all",
    )


@customers_bp.route("/")
@login_required
@permission_required("customers.view")
def index():
    return _customer_page()


@customers_bp.route("/create", methods=["GET", "POST"])
@login_required
@permission_required("customers.*")
def create():
    if request.method == "GET":
        session["open_customer_modal"] = True
        return redirect(url_for("customers.index"))

    form = CustomerForm()
    if form.validate_on_submit():
        opening_raw = form.opening_balance.data
        opening = Decimal("0") if opening_raw in (None, "") else Decimal(str(opening_raw))
        joined = form.joined_date.data or get_working_date()
        try:
            customer = Customer(
                name=form.name.data,
                phone=form.phone.data,
                cnic=form.cnic.data,
                customer_type=form.customer_type.data or "good",
                address=form.address.data,
                old_book_no=form.old_book_no.data or None,
                joined_date=joined,
                opening_balance=opening,
                credit_limit=form.credit_limit.data or 0,
                balance=opening,
                notes=form.notes.data,
            )
            _apply_customer_photo(customer)
            db.session.add(customer)
            db.session.flush()
            sync_party_opening_entry(
                "customer",
                customer.id,
                opening,
                entry_date=joined,
                notes=_opening_notes(customer),
            )
            log_audit("create", "customer", None, customer.name)
            db.session.commit()
            flash("Customer created.", "success")
            return redirect(url_for("customers.index"))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            return _customer_page(form=form, open_modal=True)
    return _customer_page(form=form, open_modal=True)


@customers_bp.route("/payment", methods=["POST"])
@login_required
@permission_required("customers.create")
def payment():
    customer_id = request.form.get("customer_id")
    payment_type = request.form.get("payment_type")
    amount = request.form.get("amount")
    payment_date = request.form.get("payment_date") or get_working_date().isoformat()
    remarks = request.form.get("remarks")

    try:
        receiving = record_customer_payment(
            customer_id=int(customer_id),
            payment_type=payment_type,
            amount=amount,
            payment_date=payment_date,
            remarks=remarks,
            user_id=current_user.id,
        )
        log_audit("create", "customer_receiving", receiving.id, payment_type)
        db.session.commit()
        flash(
            f"Payment recorded ({PAYMENT_TYPES.get(payment_type, payment_type)}).",
            "success",
        )
        if request.form.get("return_detail"):
            return redirect(url_for("customers.detail", customer_id=receiving.customer_id))
        return redirect(url_for("customers.index"))
    except (ValueError, TypeError) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        if request.form.get("return_detail") and customer_id:
            try:
                return redirect(url_for("customers.detail", customer_id=int(customer_id)))
            except (TypeError, ValueError):
                pass
        return redirect(url_for("customers.index"))


@customers_bp.route("/<int:customer_id>")
@login_required
@permission_required("customers.view")
def detail(customer_id):
    from app.services.dashboard_service import _range_for_filter

    customer = db.session.get(Customer, customer_id)
    if not customer or customer.is_deleted:
        flash("Customer not found.", "danger")
        return redirect(url_for("customers.index"))

    period = request.args.get("period", "all")
    period_start = _parse_date(request.args.get("start_date"))
    period_end = _parse_date(request.args.get("end_date"))
    range_start, range_end = _range_for_filter(period, period_start, period_end)

    ledger_q = LedgerEntry.query.filter_by(party_type="customer", party_id=customer_id)
    recv_q = CustomerReceiving.query.filter_by(customer_id=customer_id)
    if range_start:
        ledger_q = ledger_q.filter(LedgerEntry.entry_date >= range_start)
        recv_q = recv_q.filter(CustomerReceiving.receiving_date >= range_start)
    if range_end:
        ledger_q = ledger_q.filter(LedgerEntry.entry_date <= range_end)
        recv_q = recv_q.filter(CustomerReceiving.receiving_date <= range_end)

    # Heal: old account on profile must appear as Opening row; total = old + in/out
    if _ensure_customer_opening(customer):
        db.session.commit()
        ledger_q = LedgerEntry.query.filter_by(party_type="customer", party_id=customer_id)
        if range_start:
            ledger_q = ledger_q.filter(LedgerEntry.entry_date >= range_start)
        if range_end:
            ledger_q = ledger_q.filter(LedgerEntry.entry_date <= range_end)

    ledger = ledger_q.order_by(LedgerEntry.entry_date.desc(), LedgerEntry.id.desc()).all()
    receivings = recv_q.order_by(CustomerReceiving.receiving_date.desc()).all()
    return render_template(
        "customers/detail.html",
        customer=customer,
        ledger=ledger,
        receivings=receivings,
        today=get_working_date().isoformat(),
        payment_types=PAYMENT_TYPES,
        selected_period=period,
        start_date=period_start.isoformat() if period_start else "",
        end_date=period_end.isoformat() if period_end else "",
    )


@customers_bp.route("/<int:customer_id>/edit", methods=["POST"])
@login_required
@permission_required("customers.*")
def edit(customer_id):
    customer = db.session.get(Customer, customer_id)
    if not customer or customer.is_deleted:
        flash("Customer not found.", "danger")
        return redirect(url_for("customers.index"))
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Name is required.", "danger")
        return redirect(url_for("customers.index"))
    try:
        customer.name = name
        customer.phone = (request.form.get("phone") or "").strip() or None
        customer.address = (request.form.get("address") or "").strip() or None
        customer.old_book_no = (request.form.get("old_book_no") or "").strip() or None
        customer.customer_type = request.form.get("customer_type") or "good"
        joined = request.form.get("joined_date")
        if joined:
            customer.joined_date = date_cls.fromisoformat(joined)
        opening = request.form.get("opening_balance")
        customer.opening_balance = (
            Decimal("0") if opening in (None, "") else Decimal(str(opening))
        )
        _apply_customer_photo(customer)
        sync_party_opening_entry(
            "customer",
            customer.id,
            customer.opening_balance,
            entry_date=customer.joined_date or get_working_date(),
            notes=_opening_notes(customer),
        )
        log_audit("update", "customer", customer.id, customer.name)
        db.session.commit()
        flash("Customer updated.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    if request.form.get("return_detail"):
        return redirect(url_for("customers.detail", customer_id=customer.id))
    return redirect(url_for("customers.index"))


@customers_bp.route("/<int:customer_id>/delete", methods=["POST"])
@login_required
@permission_required("customers.*")
def delete(customer_id):
    customer = db.session.get(Customer, customer_id)
    if not customer or customer.is_deleted:
        flash("Customer not found.", "danger")
        return redirect(url_for("customers.index"))
    customer.is_deleted = True
    from app.models.mixins import utcnow
    customer.deleted_at = utcnow()
    log_audit("delete", "customer", customer.id, customer.name)
    db.session.commit()
    flash("Customer deleted.", "success")
    return redirect(url_for("customers.index"))


@customers_bp.route("/ledger/<int:entry_id>/edit", methods=["POST"])
@login_required
@permission_required("customers.*")
def edit_ledger(entry_id):
    try:
        entry = update_ledger_entry(
            entry_id,
            entry_date=request.form.get("entry_date"),
            debit=request.form.get("debit"),
            credit=request.form.get("credit"),
            notes=request.form.get("notes"),
            entry_type=request.form.get("entry_type"),
        )
        log_audit("update", "ledger_entry", entry.id)
        db.session.commit()
        flash("Ledger entry updated.", "success")
        return redirect(url_for("customers.detail", customer_id=entry.party_id))
    except (ValueError, TypeError) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("customers.index"))


@customers_bp.route("/ledger/<int:entry_id>/delete", methods=["POST"])
@login_required
@permission_required("customers.*")
def delete_ledger(entry_id):
    try:
        party_type, party_id, source = delete_ledger_entry_cascading(
            entry_id, current_user.id
        )
        log_audit("delete", "ledger_entry", entry_id, source)
        db.session.commit()
        messages = {
            "sale": "Sale deleted. Stock, cash, and customer ledger reversed.",
            "customer_receiving": "Payment deleted. Cash and customer balance reversed.",
            "purchase": "Purchase reversed. Stock and vendor balance updated.",
            "vendor_payment": "Vendor payment deleted and reversed.",
            "ledger": "Ledger entry deleted. Balance recalculated.",
        }
        flash(messages.get(source, "Entry deleted and balances recalculated."), "success")
        if party_type == "customer":
            return redirect(url_for("customers.detail", customer_id=party_id))
        if party_type == "vendor":
            return redirect(url_for("vendors.detail", vendor_id=party_id))
        return redirect(url_for("customers.index"))
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("customers.index"))


@customers_bp.route("/payments/<int:receiving_id>/delete", methods=["POST"])
@login_required
@permission_required("customers.*")
def delete_payment(receiving_id):
    try:
        customer_id = delete_customer_payment(receiving_id, current_user.id)
        log_audit("delete", "customer_receiving", receiving_id)
        db.session.commit()
        flash("Payment deleted and reversed.", "success")
        return redirect(url_for("customers.detail", customer_id=customer_id))
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("customers.index"))

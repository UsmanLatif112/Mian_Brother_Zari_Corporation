from datetime import date, datetime
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from app.extensions import db
from app.models import AccountAmountTaken
from app.services.account_service import (
    computed_cash_in_hand,
    create_amount_taken,
    delete_amount_taken,
    get_latest_setup,
    save_setup,
    update_amount_taken,
)
from app.services.audit_service import log_audit
from app.utils.decorators import permission_required
from app.utils.working_date import get_working_date

account_bp = Blueprint("account", __name__)


def _parse_date(raw, default=None):
    raw = (raw or "").strip()
    if not raw:
        return default
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return default


def _parse_amount(raw) -> Decimal:
    return Decimal(str(raw or 0))


@account_bp.route("/")
@login_required
@permission_required("cashbook.view")
def index():
    from app.services.dashboard_service import _range_for_filter

    period = request.args.get("period", "all")
    period_start = _parse_date(request.args.get("start_date"))
    period_end = _parse_date(request.args.get("end_date"))
    range_start, range_end = _range_for_filter(period, period_start, period_end)

    query = AccountAmountTaken.query.filter_by(is_deleted=False)
    if range_start:
        query = query.filter(AccountAmountTaken.taken_date >= range_start)
    if range_end:
        query = query.filter(AccountAmountTaken.taken_date <= range_end)

    rows = query.order_by(
        AccountAmountTaken.taken_date.desc(),
        AccountAmountTaken.id.desc(),
    ).all()

    total_taken = (
        db.session.query(func.coalesce(func.sum(AccountAmountTaken.amount), 0))
        .filter(AccountAmountTaken.is_deleted.is_(False))
        .scalar()
    ) or Decimal("0")
    filtered_total = sum((Decimal(str(r.amount or 0)) for r in rows), Decimal("0"))

    setup = get_latest_setup()
    cash_snap = computed_cash_in_hand(period=period, start_date=period_start, end_date=period_end)
    prev_bal = cash_snap["previous_balance"]
    cash_hand = cash_snap["cash_in_hand"]

    return render_template(
        "account/index.html",
        rows=rows,
        setup=setup,
        previous_balance=prev_bal,
        cash_in_hand=cash_hand,
        total_taken=total_taken,
        filtered_total=filtered_total,
        selected_period=period,
        start_date=period_start.isoformat() if period_start else "",
        end_date=period_end.isoformat() if period_end else "",
        today=get_working_date().isoformat(),
        open_take_modal=request.args.get("open_take") == "1",
        open_setup_modal=request.args.get("open_setup") == "1",
    )


@account_bp.route("/setup", methods=["POST"])
@login_required
@permission_required("cashbook.*")
def setup():
    balance_date = _parse_date(request.form.get("balance_date"), get_working_date())
    try:
        amount = _parse_amount(request.form.get("previous_balance"))
        if amount < 0:
            raise ValueError("Previous balance cannot be negative.")
        row = save_setup(
            balance_date=balance_date,
            previous_balance=amount,
            user_id=current_user.id,
            notes=request.form.get("notes"),
        )
        log_audit("setup", "account_cash_setup", row.id, f"{row.balance_date}: {row.previous_balance}")
        db.session.commit()
        flash("Previous balance saved.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("account.index"))


@account_bp.route("/take", methods=["POST"])
@login_required
@permission_required("cashbook.*")
def take():
    try:
        taken_date = _parse_date(request.form.get("taken_date"), get_working_date())
        row = create_amount_taken(
            taken_date=taken_date,
            taken_by=request.form.get("taken_by"),
            amount=_parse_amount(request.form.get("amount")),
            user_id=current_user.id,
            notes=request.form.get("notes"),
        )
        log_audit("create", "amount_taken", row.id, f"{row.taken_by}: {row.amount}")
        db.session.commit()
        flash("Amount taken recorded.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("account.index", open_take=1))
    return redirect(url_for("account.index"))


@account_bp.route("/<int:row_id>/edit", methods=["POST"])
@login_required
@permission_required("cashbook.*")
def edit(row_id):
    try:
        taken_date = _parse_date(request.form.get("taken_date"), get_working_date())
        row = update_amount_taken(
            row_id,
            taken_date=taken_date,
            taken_by=request.form.get("taken_by"),
            amount=_parse_amount(request.form.get("amount")),
            user_id=current_user.id,
            notes=request.form.get("notes"),
        )
        log_audit("update", "amount_taken", row.id, f"{row.taken_by}: {row.amount}")
        db.session.commit()
        flash("Record updated.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("account.index"))


@account_bp.route("/<int:row_id>/delete", methods=["POST"])
@login_required
@permission_required("cashbook.*")
def delete(row_id):
    try:
        delete_amount_taken(row_id, user_id=current_user.id)
        log_audit("delete", "amount_taken", row_id, None)
        db.session.commit()
        flash("Record deleted.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("account.index"))

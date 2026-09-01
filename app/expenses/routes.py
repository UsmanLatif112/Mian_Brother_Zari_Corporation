from datetime import date
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.forms import ExpenseForm
from app.models import Expense, ExpenseCategory
from app.services.audit_service import log_audit
from app.services.expense_service import delete_expense, record_expense_cash_out, settle_expense, update_expense
from app.utils.decorators import permission_required
from app.utils.working_date import get_working_date

expenses_bp = Blueprint("expenses", __name__)


def _expense_form():
    form = ExpenseForm()
    form.category_id.choices = [
        (c.id, c.name)
        for c in ExpenseCategory.query.filter_by(is_deleted=False).order_by(ExpenseCategory.name)
    ]
    if not form.expense_date.data:
        form.expense_date.data = get_working_date()
    return form


def _parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _expense_page(form=None, open_modal=False):
    from app.services.dashboard_service import _range_for_filter

    period = request.args.get("period", "all")
    period_start = _parse_date(request.args.get("start_date"))
    period_end = _parse_date(request.args.get("end_date"))
    range_start, range_end = _range_for_filter(period, period_start, period_end)
    category_id = request.args.get("category_id", type=int)

    query = Expense.query.filter_by(is_deleted=False)
    if range_start:
        query = query.filter(Expense.expense_date >= range_start)
    if range_end:
        query = query.filter(Expense.expense_date <= range_end)
    if category_id:
        query = query.filter(Expense.category_id == category_id)

    expenses = query.order_by(Expense.expense_date.desc(), Expense.id.desc()).all()

    from app.services.expense_analytics_service import expense_page_analytics

    expense_summary = expense_page_analytics(
        range_start=range_start,
        range_end=range_end,
        category_id=category_id,
    )
    categories = ExpenseCategory.query.filter_by(is_deleted=False).order_by(ExpenseCategory.name).all()
    return render_template(
        "expenses/index.html",
        expenses=expenses,
        expense_summary=expense_summary,
        total_count=len(expenses),
        form=form or _expense_form(),
        categories=categories,
        today=get_working_date().isoformat(),
        open_modal=open_modal or request.args.get("open_modal") == "1",
        selected_period=period,
        start_date=period_start.isoformat() if period_start else "",
        end_date=period_end.isoformat() if period_end else "",
        selected_category=category_id or "",
    )


@expenses_bp.route("/")
@login_required
@permission_required("expenses.view")
def index():
    return _expense_page()


@expenses_bp.route("/create", methods=["GET", "POST"])
@login_required
@permission_required("expenses.create")
def create():
    if request.method == "GET":
        return redirect(url_for("expenses.index", open_modal=1))

    expense_date_raw = request.form.get("expense_date") or get_working_date().isoformat()
    try:
        expense_date = date.fromisoformat(expense_date_raw)
    except ValueError:
        flash("Invalid date.", "danger")
        return _expense_page(open_modal=True)

    names = request.form.getlist("name")
    amounts = request.form.getlist("amount")
    category_ids = request.form.getlist("category_id")
    descriptions = request.form.getlist("description")

    created = 0
    try:
        for i, name in enumerate(names):
            name = (name or "").strip()
            if not name:
                continue
            amount_raw = amounts[i] if i < len(amounts) else "0"
            amount = Decimal(str(amount_raw or 0))
            if amount <= 0:
                raise ValueError(f"Amount must be greater than zero for “{name}”.")
            cat_raw = category_ids[i] if i < len(category_ids) else ""
            if not cat_raw:
                raise ValueError(f"Select a category for “{name}”.")
            category_id = int(cat_raw)
            desc = (descriptions[i] if i < len(descriptions) else "") or None
            if desc:
                desc = desc.strip() or None
            expense = Expense(
                name=name,
                description=desc,
                amount=amount,
                category_id=category_id,
                expense_date=expense_date,
                created_by_id=current_user.id,
            )
            db.session.add(expense)
            db.session.flush()
            record_expense_cash_out(expense, current_user.id)
            log_audit("create", "expense", expense.id, expense.name)
            created += 1

        if created == 0:
            raise ValueError("Add at least one expense row with a name and amount.")

        db.session.commit()
        flash(f"{created} expense{'s' if created != 1 else ''} added.", "success")
        return redirect(url_for("expenses.index"))
    except (ValueError, TypeError, ArithmeticError) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return _expense_page(open_modal=True)


@expenses_bp.route("/<int:expense_id>/settle", methods=["POST"])
@login_required
@permission_required("expenses.*")
def settle(expense_id):
    try:
        settle_expense(
            expense_id,
            current_user.id,
            notes=request.form.get("notes"),
            amount=request.form.get("amount"),
            payment_date=request.form.get("payment_date"),
        )
        db.session.commit()
        flash("Expense settled.", "success")
    except (ValueError, TypeError, ArithmeticError) as e:
        db.session.rollback()
        flash(str(e), "danger")
    return redirect(url_for("expenses.index"))


@expenses_bp.route("/<int:expense_id>/edit", methods=["POST"])
@login_required
@permission_required("expenses.*")
def edit(expense_id):
    try:
        update_expense(
            expense_id,
            name=request.form.get("name"),
            description=request.form.get("description"),
            amount=request.form.get("amount"),
            category_id=request.form.get("category_id"),
            expense_date=request.form.get("expense_date"),
        )
        db.session.commit()
        flash("Expense updated.", "success")
    except (ValueError, TypeError, ArithmeticError) as e:
        db.session.rollback()
        flash(str(e), "danger")
    return redirect(url_for("expenses.index"))


@expenses_bp.route("/<int:expense_id>/delete", methods=["POST"])
@login_required
@permission_required("expenses.*")
def delete(expense_id):
    try:
        delete_expense(expense_id, current_user.id)
        db.session.commit()
        flash("Expense deleted.", "success")
    except ValueError as e:
        db.session.rollback()
        flash(str(e), "danger")
    return redirect(url_for("expenses.index"))

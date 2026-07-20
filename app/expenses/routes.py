from datetime import date
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from app.extensions import db
from app.forms import ExpenseForm
from app.models import Expense, ExpenseCategory
from app.services.audit_service import log_audit
from app.services.expense_service import delete_expense, settle_expense, update_expense
from app.utils.decorators import permission_required

expenses_bp = Blueprint("expenses", __name__)


def _expense_form():
    form = ExpenseForm()
    form.category_id.choices = [(c.id, c.name) for c in ExpenseCategory.query.all()]
    if not form.expense_date.data:
        form.expense_date.data = date.today()
    return form


def _expense_page(form=None, open_modal=False):
    expenses = (
        Expense.query.filter_by(is_deleted=False)
        .order_by(Expense.expense_date.desc(), Expense.id.desc())
        .all()
    )
    total_amount = (
        db.session.query(func.coalesce(func.sum(Expense.amount), 0))
        .filter(Expense.is_deleted.is_(False))
        .scalar()
    ) or Decimal("0")
    settled_amount = (
        db.session.query(func.coalesce(func.sum(Expense.amount), 0))
        .filter(Expense.is_deleted.is_(False), Expense.is_settled.is_(True))
        .scalar()
    ) or Decimal("0")
    pending_amount = (
        db.session.query(func.coalesce(func.sum(Expense.amount), 0))
        .filter(Expense.is_deleted.is_(False), Expense.is_settled.is_(False))
        .scalar()
    ) or Decimal("0")
    pending_count = Expense.query.filter_by(is_deleted=False, is_settled=False).count()
    categories = ExpenseCategory.query.order_by(ExpenseCategory.name).all()
    return render_template(
        "expenses/index.html",
        expenses=expenses,
        total_amount=total_amount,
        settled_amount=settled_amount,
        pending_amount=pending_amount,
        pending_count=pending_count,
        total_count=len(expenses),
        form=form or _expense_form(),
        categories=categories,
        today=date.today().isoformat(),
        open_modal=open_modal or request.args.get("open_modal") == "1",
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

    expense_date_raw = request.form.get("expense_date") or date.today().isoformat()
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

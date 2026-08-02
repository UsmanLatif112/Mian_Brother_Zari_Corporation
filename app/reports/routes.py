from datetime import date

from flask import Blueprint, make_response, render_template, request
from flask_login import login_required

from app.extensions import db
from app.models import CashBookEntry, Expense, Purchase, Sale
from app.utils.decorators import permission_required

reports_bp = Blueprint("reports", __name__)


@reports_bp.route("/")
@login_required
@permission_required("reports.view")
def index():
    return render_template("reports/index.html")


@reports_bp.route("/sales")
@login_required
@permission_required("reports.view")
def sales_report():
    start = request.args.get("start")
    end = request.args.get("end")
    q = Sale.query
    if start:
        q = q.filter(Sale.sale_date >= date.fromisoformat(start))
    if end:
        q = q.filter(Sale.sale_date <= date.fromisoformat(end))
    sales = q.order_by(Sale.sale_date.desc()).all()
    return render_template("reports/sales.html", sales=sales, start=start, end=end)


@reports_bp.route("/cashbook")
@login_required
@permission_required("cashbook.view")
def cashbook():
    entries = CashBookEntry.query.order_by(CashBookEntry.entry_date.desc()).limit(500).all()
    return render_template("reports/cashbook.html", entries=entries)


@reports_bp.route("/daily-closing")
@login_required
@permission_required("reports.view")
def daily_closing():
    from sqlalchemy import func

    from app.models import CustomerReceiving
    from app.utils.working_date import get_working_date

    today = get_working_date()

    cash_sales = (
        db.session.query(func.coalesce(func.sum(Sale.grand_total), 0))
        .filter(Sale.sale_date == today)
        .scalar()
    )
    receivings = (
        db.session.query(func.coalesce(func.sum(CustomerReceiving.amount), 0))
        .filter(CustomerReceiving.receiving_date == today)
        .scalar()
    )
    expenses = (
        db.session.query(func.coalesce(func.sum(Expense.amount), 0))
        .filter(Expense.expense_date == today, Expense.is_settled.is_(True))
        .scalar()
    )
    return render_template(
        "reports/daily_closing.html",
        today=today,
        cash_sales=cash_sales,
        receivings=receivings,
        expenses=expenses,
    )

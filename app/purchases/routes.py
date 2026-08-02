from datetime import date
from decimal import Decimal

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import Purchase, PurchaseItem
from app.utils.decorators import permission_required
from app.utils.working_date import get_working_date

purchases_bp = Blueprint("purchases", __name__)


def _parse_date(value):
    if not value:
        return None
    return date.fromisoformat(value)


@purchases_bp.route("/")
@login_required
@permission_required("purchases.view")
def index():
    from app.services.dashboard_service import _range_for_filter

    period = request.args.get("period", "all")
    period_start = _parse_date(request.args.get("start_date"))
    period_end = _parse_date(request.args.get("end_date"))
    range_start, range_end = _range_for_filter(period, period_start, period_end)

    query = Purchase.query.options(
        joinedload(Purchase.vendor),
        joinedload(Purchase.items).joinedload(PurchaseItem.product),
    )
    if range_start:
        query = query.filter(Purchase.purchase_date >= range_start)
    if range_end:
        query = query.filter(Purchase.purchase_date <= range_end)

    purchases = query.order_by(Purchase.purchase_date.desc(), Purchase.id.desc()).all()

    from app.services.journal_service import items_particulars

    for p in purchases:
        p.particulars = items_particulars(p.items, fallback="—", kind="purchase")

    totals_q = db.session.query(
        func.coalesce(func.sum(Purchase.grand_total), 0),
        func.count(Purchase.id),
    )
    if range_start:
        totals_q = totals_q.filter(Purchase.purchase_date >= range_start)
    if range_end:
        totals_q = totals_q.filter(Purchase.purchase_date <= range_end)
    total_purchasing, purchase_count = totals_q.one()
    total_purchasing = total_purchasing or Decimal("0")
    purchase_count = purchase_count or 0

    return render_template(
        "purchases/index.html",
        purchases=purchases,
        total_purchasing=total_purchasing,
        purchase_count=purchase_count,
        selected_period=period,
        start_date=period_start.isoformat() if period_start else "",
        end_date=period_end.isoformat() if period_end else "",
        today=get_working_date().isoformat(),
    )


@purchases_bp.route("/create", methods=["GET", "POST"])
@login_required
@permission_required("purchases.view")
def create():
    """Purchases are created from Inventory additions — this page is view-only."""
    return redirect(url_for("purchases.index"))

from datetime import date

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy.orm import joinedload

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
    from app.services.purchase_analytics_service import purchase_page_chart_metrics

    for p in purchases:
        p.particulars = items_particulars(p.items, fallback="—", kind="purchase")

    purchase_metrics = purchase_page_chart_metrics(
        period=period,
        start_date=period_start,
        end_date=period_end,
    )

    return render_template(
        "purchases/index.html",
        purchases=purchases,
        total_purchasing=purchase_metrics["total_purchasing"],
        total_paid=purchase_metrics["total_paid"],
        total_payable=purchase_metrics["total_payable"],
        purchase_count=purchase_metrics["purchase_count"],
        purchases_chart=purchase_metrics["chart"],
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

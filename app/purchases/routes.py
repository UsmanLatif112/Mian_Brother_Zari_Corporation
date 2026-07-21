from datetime import date

from flask import Blueprint, redirect, render_template, url_for
from flask_login import login_required
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import Purchase, PurchaseItem
from app.utils.decorators import permission_required

purchases_bp = Blueprint("purchases", __name__)


@purchases_bp.route("/")
@login_required
@permission_required("purchases.view")
def index():
    purchases = (
        Purchase.query.options(
            joinedload(Purchase.vendor),
            joinedload(Purchase.items).joinedload(PurchaseItem.product),
        )
        .order_by(Purchase.purchase_date.desc(), Purchase.id.desc())
        .limit(200)
        .all()
    )
    total_purchasing = (
        db.session.query(func.coalesce(func.sum(Purchase.grand_total), 0)).scalar() or 0
    )
    purchase_count = db.session.query(func.count(Purchase.id)).scalar() or 0
    return render_template(
        "purchases/index.html",
        purchases=purchases,
        total_purchasing=total_purchasing,
        purchase_count=purchase_count,
        today=date.today().isoformat(),
    )


@purchases_bp.route("/create", methods=["GET", "POST"])
@login_required
@permission_required("purchases.view")
def create():
    """Purchases are created from Inventory additions — this page is view-only."""
    return redirect(url_for("purchases.index"))

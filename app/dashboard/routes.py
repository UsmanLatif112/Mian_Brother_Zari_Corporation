from datetime import date

from flask import Blueprint, render_template, request
from flask_login import login_required

from app.models import Notification
from app.services.dashboard_service import get_dashboard_metrics
from app.utils.decorators import permission_required

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
@login_required
@permission_required("dashboard.view")
def index():
    period = request.args.get("period", "all")
    start_raw = request.args.get("start_date")
    end_raw = request.args.get("end_date")
    start_date = date.fromisoformat(start_raw) if start_raw else None
    end_date = date.fromisoformat(end_raw) if end_raw else None
    metrics = get_dashboard_metrics(period=period, start_date=start_date, end_date=end_date)
    notifications = (
        Notification.query.filter_by(is_read=False)
        .order_by(Notification.created_at.desc())
        .limit(10)
        .all()
    )
    return render_template(
        "dashboard/index.html",
        metrics=metrics,
        notifications=notifications,
        selected_period=period,
        start_date=start_raw or "",
        end_date=end_raw or "",
    )

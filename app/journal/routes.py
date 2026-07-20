from datetime import date

from flask import Blueprint, render_template, request
from flask_login import login_required

from app.services.journal_service import get_general_journal
from app.utils.decorators import permission_required

journal_bp = Blueprint("journal", __name__)


def _parse_date(value):
    if not value:
        return None
    return date.fromisoformat(value)


@journal_bp.route("/")
@login_required
@permission_required("reports.view")
def index():
    period = request.args.get("period", "all")
    start_raw = request.args.get("start_date") or ""
    end_raw = request.args.get("end_date") or ""
    start_date = _parse_date(start_raw)
    end_date = _parse_date(end_raw)

    data = get_general_journal(period=period, start_date=start_date, end_date=end_date)

    return render_template(
        "journal/index.html",
        rows=data["rows"],
        total_in=data["total_in"],
        total_out=data["total_out"],
        net=data["net"],
        count=data["count"],
        selected_period=period,
        start_date=start_raw,
        end_date=end_raw,
    )

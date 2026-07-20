from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required

from app.extensions import db
from app.models import AuditLog, Setting
from app.services.audit_service import log_audit
from app.services.settings_service import get_setting, set_setting
from app.utils.decorators import permission_required

settings_bp = Blueprint("settings", __name__)


@settings_bp.route("/")
@login_required
@permission_required("settings.view")
def index():
    keys = [
        "company_name",
        "company_address",
        "company_phone",
        "company_email",
        "currency",
        "date_format",
        "tax_rate",
    ]
    values = {k: get_setting(k, "") for k in keys}
    return render_template("settings/index.html", values=values)


@settings_bp.route("/save", methods=["POST"])
@login_required
@permission_required("settings.view")
def save():
    for key in request.form:
        if key != "csrf_token":
            set_setting(key, request.form[key])
    log_audit("update", "settings", None)
    db.session.commit()
    flash("Settings saved.", "success")
    return redirect(url_for("settings.index"))


@settings_bp.route("/audit")
@login_required
@permission_required("settings.view")
def audit():
    logs = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(200).all()
    return render_template("settings/audit.html", logs=logs)

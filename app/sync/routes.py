from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import SyncLog, SyncQueue
from app.services.audit_service import log_audit
from app.services.sync_service import is_mysql_available, run_sync
from app.utils.decorators import permission_required

sync_bp = Blueprint("sync", __name__)


@sync_bp.route("/")
@login_required
@permission_required("sync.view")
def index():
    logs = SyncLog.query.order_by(SyncLog.started_at.desc()).limit(30).all()
    queue = SyncQueue.query.filter_by(is_resolved=False).order_by(SyncQueue.id).limit(50).all()
    online = is_mysql_available()
    return render_template("sync/index.html", logs=logs, queue=queue, online=online)


@sync_bp.route("/run", methods=["POST"])
@login_required
@permission_required("sync.view")
def run():
    log = run_sync(current_user.id)
    log_audit("sync", "database", log.id, log.message)
    db.session.commit()
    flash(log.message or "Sync completed.", "success" if log.status == "success" else "warning")
    return redirect(url_for("sync.index"))

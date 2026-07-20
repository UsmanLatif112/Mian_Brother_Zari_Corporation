from datetime import date
import os

from flask import Blueprint, flash, redirect, render_template, send_file, url_for
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import SyncLog, SyncQueue
from app.services.audit_service import log_audit
from app.services.backup_service import backup_sqlite, ensure_backup_dir
from app.services.sync_service import is_mysql_available, run_sync
from app.utils.decorators import permission_required

maintenance_bp = Blueprint("maintenance", __name__)


@maintenance_bp.route("/", methods=["GET"])
@login_required
def index():
    logs = SyncLog.query.order_by(SyncLog.started_at.desc()).limit(30).all()
    queue = SyncQueue.query.filter_by(is_resolved=False).order_by(SyncQueue.id).limit(50).all()
    online = is_mysql_available()
    can_sync = current_user.has_permission("sync.view")
    can_backup = current_user.has_permission("backup.view")

    backup_dir = ensure_backup_dir()
    backups = sorted(
        [f for f in os.listdir(backup_dir) if f.endswith(".db")],
        reverse=True,
    )

    return render_template(
        "maintenance/index.html",
        logs=logs,
        queue=queue,
        online=online,
        can_sync=can_sync,
        can_backup=can_backup,
        backups=backups,
        today=date.today().isoformat(),
    )


@maintenance_bp.route("/sync", methods=["POST"])
@login_required
@permission_required("sync.view")
def push_sync():
    log = run_sync()
    log_audit("sync", "database", log.id, log.message)
    db.session.commit()
    flash(log.message or "Sync completed.", "success" if log.status == "success" else "warning")
    return redirect(url_for("maintenance.index"))


@maintenance_bp.route("/backup", methods=["POST"])
@login_required
@permission_required("backup.view")
def create_backup():
    try:
        path = backup_sqlite()
        log_audit("backup", "database", None, path)
        db.session.commit()
        flash("Backup created successfully.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("maintenance.index"))


@maintenance_bp.route("/download/<filename>", methods=["GET"])
@login_required
@permission_required("backup.view")
def download(filename: str):
    filename = secure_filename(filename)
    path = os.path.join(ensure_backup_dir(), filename)
    if not os.path.isfile(path):
        flash("Backup file not found.", "danger")
        return redirect(url_for("maintenance.index"))
    return send_file(path, as_attachment=True)


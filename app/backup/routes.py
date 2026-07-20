import os

from flask import Blueprint, flash, redirect, render_template, send_file, url_for
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from app.services.audit_service import log_audit
from app.services.backup_service import backup_sqlite, ensure_backup_dir
from app.extensions import db
from app.utils.decorators import permission_required

backup_bp = Blueprint("backup", __name__)


@backup_bp.route("/")
@login_required
@permission_required("backup.view")
def index():
    backup_dir = ensure_backup_dir()
    files = sorted(
        [f for f in os.listdir(backup_dir) if f.endswith(".db")],
        reverse=True,
    )
    return render_template("backup/index.html", backups=files)


@backup_bp.route("/run", methods=["POST"])
@login_required
@permission_required("backup.view")
def run_backup():
    try:
        path = backup_sqlite()
        log_audit("backup", "database", None, path)
        db.session.commit()
        flash("Backup created successfully.", "success")
    except Exception as e:
        flash(str(e), "danger")
    return redirect(url_for("backup.index"))


@backup_bp.route("/download/<filename>")
@login_required
@permission_required("backup.view")
def download(filename):
    filename = secure_filename(filename)
    path = os.path.join(ensure_backup_dir(), filename)
    if not os.path.isfile(path):
        flash("Backup file not found.", "danger")
        return redirect(url_for("backup.index"))
    return send_file(path, as_attachment=True)

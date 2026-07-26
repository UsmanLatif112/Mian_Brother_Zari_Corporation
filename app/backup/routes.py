import os

from flask import Blueprint, flash, redirect, render_template, send_file, url_for
from flask_login import login_required

from app.extensions import db
from app.services.audit_service import log_audit
from app.services.backup_service import (
    backup_sqlite,
    ensure_backup_dir,
    list_backup_files,
    resolve_backup_entry,
    zip_backup_folder,
)
from app.utils.decorators import permission_required

backup_bp = Blueprint("backup", __name__)


@backup_bp.route("/")
@login_required
@permission_required("backup.view")
def index():
    backups, _ = list_backup_files()
    return render_template("backup/index.html", backups=backups)


@backup_bp.route("/run", methods=["POST"])
@login_required
@permission_required("backup.view")
def run_backup():
    try:
        path = backup_sqlite()
        log_audit("backup", "database", None, path)
        db.session.commit()
        flash(f"Backup folder created: {os.path.basename(path)}", "success")
    except Exception as e:
        flash(str(e), "danger")
    return redirect(url_for("backup.index"))


@backup_bp.route("/download/<path:filename>")
@login_required
@permission_required("backup.view")
def download(filename):
    name = os.path.basename(filename)
    try:
        path = resolve_backup_entry(name)
    except FileNotFoundError:
        flash("Backup not found.", "danger")
        return redirect(url_for("backup.index"))
    if os.path.isdir(path):
        zip_path = zip_backup_folder(path)
        return send_file(zip_path, as_attachment=True, download_name=os.path.basename(zip_path))
    if not os.path.isfile(path):
        flash("Backup file not found.", "danger")
        return redirect(url_for("backup.index"))
    return send_file(path, as_attachment=True)

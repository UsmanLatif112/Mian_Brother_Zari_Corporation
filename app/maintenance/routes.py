from datetime import date
import os

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import SyncLog, SyncQueue
from app.services.audit_service import log_audit
from app.services.backup_service import (
    backup_sqlite,
    commit_after_backup,
    copy_backup_to_dest,
    ensure_backup_dir,
    list_backup_files,
    resolve_backup_entry,
    zip_backup_folder,
)
from app.services.sync_service import is_mysql_available, run_sync
from app.utils.decorators import permission_required

maintenance_bp = Blueprint("maintenance", __name__)


def _is_desktop() -> bool:
    return os.environ.get("DESKTOP_APP", "").lower() in ("1", "true", "yes")


def _safe_user_dest(raw: str | None) -> str | None:
    """
    Accept an absolute save path from the desktop app (native Save dialog).
    Also allowed for local loopback requests so localhost browser + bridge works.
    """
    path = (raw or "").strip()
    if not path:
        return None
    remote = (request.remote_addr or "").strip()
    local = remote in ("127.0.0.1", "::1", "localhost")
    if not (_is_desktop() or local):
        return None
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    if not parent or parent == path:
        return None
    return path


@maintenance_bp.route("/", methods=["GET"])
@login_required
def index():
    logs = SyncLog.query.order_by(SyncLog.started_at.desc()).limit(30).all()
    queue = SyncQueue.query.filter_by(is_resolved=False).order_by(SyncQueue.id).limit(50).all()
    online = is_mysql_available()
    can_sync = current_user.has_permission("sync.view")
    can_backup = current_user.has_permission("backup.view")

    backup_dir = ensure_backup_dir()
    ui_limit = int(current_app.config.get("BACKUP_UI_LIMIT", 10) or 10)
    backups, backup_total = list_backup_files(limit=ui_limit)

    return render_template(
        "maintenance/index.html",
        logs=logs,
        queue=queue,
        online=online,
        can_sync=can_sync,
        can_backup=can_backup,
        backups=backups,
        backup_total=backup_total,
        backup_ui_limit=ui_limit,
        backup_dir=backup_dir,
        auto_backup_hours=int(current_app.config.get("AUTO_BACKUP_INTERVAL_HOURS", 1) or 1),
        auto_backup_enabled=bool(current_app.config.get("AUTO_BACKUP_ENABLED", True)),
        is_desktop=_is_desktop(),
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
    wants_json = (
        request.headers.get("X-Requested-With") == "fetch"
        or request.form.get("ajax") == "1"
    )
    dest_path = _safe_user_dest(request.form.get("dest_path"))
    try:
        path = backup_sqlite(dest_path=dest_path)
        folder_name = os.path.basename(str(path).rstrip("\\/"))
        backups, _ = list_backup_files(limit=1)
        app_name = backups[0] if backups else folder_name

        # Backup files are already written — audit must not fail the request
        try:
            log_audit("backup", "database", None, path)
            commit_after_backup()
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass

        download_url = url_for("maintenance.download", filename=app_name)
        if wants_json:
            return jsonify(
                {
                    "ok": True,
                    "path": path,
                    "filename": app_name,
                    "saved_to": path,
                    "download_url": download_url,
                    "chose_folder": bool(dest_path),
                    "is_folder": os.path.isdir(
                        os.path.join(ensure_backup_dir(), app_name)
                    ),
                }
            )
        if dest_path:
            flash(f"Backup saved to: {path}", "success")
        else:
            flash(
                f"Backup folder created: {app_name} (includes .db + .wal + .shm when present).",
                "success",
            )
    except Exception as exc:
        try:
            db.session.rollback()
        except Exception:
            pass
        if wants_json:
            return jsonify({"ok": False, "error": str(exc)}), 400
        flash(str(exc), "danger")
    return redirect(url_for("maintenance.index"))


@maintenance_bp.route("/download/<path:filename>", methods=["GET"])
@login_required
@permission_required("backup.view")
def download(filename: str):
    """Download a backup folder as .zip, or a legacy single .db file."""
    name = os.path.basename(filename)
    try:
        path = resolve_backup_entry(name)
    except FileNotFoundError:
        flash("Backup not found.", "danger")
        return redirect(url_for("maintenance.index"))

    if os.path.isdir(path):
        zip_path = zip_backup_folder(path)
        return send_file(
            zip_path,
            as_attachment=True,
            download_name=os.path.basename(zip_path),
        )

    if not os.path.isfile(path):
        flash("Backup file not found.", "danger")
        return redirect(url_for("maintenance.index"))
    return send_file(path, as_attachment=True, download_name=name)


@maintenance_bp.route("/save-as/<path:filename>", methods=["POST"])
@login_required
@permission_required("backup.view")
def save_as(filename: str):
    """Copy an existing app backup (folder or .db) to a user-chosen path."""
    name = os.path.basename(filename)
    wants_json = request.headers.get("X-Requested-With") == "fetch" or request.form.get("ajax") == "1"
    dest_path = _safe_user_dest(request.form.get("dest_path"))
    try:
        resolve_backup_entry(name)
    except FileNotFoundError:
        msg = "Backup not found."
        if wants_json:
            return jsonify({"ok": False, "error": msg}), 404
        flash(msg, "danger")
        return redirect(url_for("maintenance.index"))
    if not dest_path:
        msg = "Choose a save location (desktop app) or use Download."
        if wants_json:
            return jsonify({"ok": False, "error": msg}), 400
        flash(msg, "danger")
        return redirect(url_for("maintenance.index"))
    try:
        saved = copy_backup_to_dest(name, dest_path)
        log_audit("backup_save_as", "database", None, saved)
        db.session.commit()
        if wants_json:
            return jsonify({"ok": True, "saved_to": saved})
        flash(f"Backup saved to: {saved}", "success")
    except Exception as exc:
        db.session.rollback()
        if wants_json:
            return jsonify({"ok": False, "error": str(exc)}), 400
        flash(str(exc), "danger")
    return redirect(url_for("maintenance.index"))

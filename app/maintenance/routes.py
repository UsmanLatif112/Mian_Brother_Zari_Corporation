from datetime import date
import os

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.services.audit_service import log_audit
from app.services.backup_service import (
    backup_sqlite,
    commit_after_backup,
    copy_backup_to_dest,
    ensure_backup_dir,
    list_backup_files,
    resolve_backup_entry,
    restore_sqlite,
    zip_backup_folder,
)
from app.services import google_drive_service as drive
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


def _drive_auto_upload_enabled() -> bool:
    return bool(current_app.config.get("GOOGLE_DRIVE_AUTO_UPLOAD", True))


def _upload_latest_backup_to_drive(folder_path: str) -> str:
    """Upload now or queue for later. Returns: uploaded | queued | skipped."""
    if not _drive_auto_upload_enabled():
        return "skipped"
    return drive.upload_or_queue_folder(folder_path)


@maintenance_bp.route("/", methods=["GET"])
@login_required
def index():
    can_backup = current_user.has_permission("backup.view")
    drive_info = drive.connection_info()
    drive_backups: list[dict] = []
    drive_queue: list[dict] = []
    if can_backup and drive_info.get("configured"):
        drive_queue = drive.list_upload_queue()
    if can_backup and drive_info.get("connected"):
        try:
            ui_limit = int(current_app.config.get("GOOGLE_DRIVE_RETENTION", 10) or 10)
            drive_backups = drive.list_drive_backups(limit=ui_limit)
        except Exception:
            drive_backups = []

    backup_dir = ensure_backup_dir()
    ui_limit = int(current_app.config.get("BACKUP_UI_LIMIT", 10) or 10)
    backups, backup_total = list_backup_files(limit=ui_limit)

    return render_template(
        "maintenance/index.html",
        can_backup=can_backup,
        backups=backups,
        backup_total=backup_total,
        backup_ui_limit=ui_limit,
        backup_dir=backup_dir,
        auto_backup_hours=int(current_app.config.get("AUTO_BACKUP_INTERVAL_HOURS", 1) or 1),
        auto_backup_enabled=bool(current_app.config.get("AUTO_BACKUP_ENABLED", True)),
        drive=drive_info,
        drive_backups=drive_backups,
        drive_retention=int(current_app.config.get("GOOGLE_DRIVE_RETENTION", 10) or 10),
        drive_auto_upload=_drive_auto_upload_enabled(),
        drive_queue=drive_queue,
        drive_queue_interval_minutes=int(
            current_app.config.get("GOOGLE_DRIVE_QUEUE_INTERVAL_MINUTES", 5) or 5
        ),
        is_desktop=_is_desktop(),
        today=date.today().isoformat(),
    )


@maintenance_bp.route("/google/connect", methods=["GET"])
@login_required
@permission_required("backup.view")
def google_connect():
    if not drive.is_configured():
        flash("Google Drive is not configured. Add GOOGLE_DRIVE_CLIENT_ID and GOOGLE_DRIVE_CLIENT_SECRET to .env.", "warning")
        return redirect(url_for("maintenance.index"))
    from app.services.network_service import is_internet_available

    if not is_internet_available():
        flash(
            "No internet connection. Connect to the internet first, then try Connect Google Drive again. "
            "The app still works offline — backups are saved locally.",
            "warning",
        )
        return redirect(url_for("maintenance.index"))
    try:
        return redirect(drive.start_auth_flow())
    except Exception as exc:
        flash(str(exc), "danger")
        return redirect(url_for("maintenance.index"))


@maintenance_bp.route("/google/connect-url", methods=["GET"])
@login_required
@permission_required("backup.view")
def google_connect_url():
    """Return OAuth URL as JSON so the UI can block offline users without leaving the app."""
    wants_json = request.headers.get("X-Requested-With") == "fetch"
    if not drive.is_configured():
        msg = "Google Drive is not configured. Add GOOGLE_DRIVE_CLIENT_ID and GOOGLE_DRIVE_CLIENT_SECRET to .env."
        if wants_json:
            return jsonify({"ok": False, "error": msg}), 400
        flash(msg, "warning")
        return redirect(url_for("maintenance.index"))

    from app.services.network_service import is_internet_available

    if not is_internet_available():
        msg = (
            "No internet connection. Connect to Wi‑Fi or mobile data first. "
            "You can keep using the app offline — local backups still work."
        )
        if wants_json:
            return jsonify({"ok": False, "offline": True, "error": msg}), 503
        flash(msg, "warning")
        return redirect(url_for("maintenance.index"))

    try:
        auth_url = drive.start_auth_flow()
        if wants_json:
            return jsonify({"ok": True, "auth_url": auth_url})
        return redirect(auth_url)
    except Exception as exc:
        if wants_json:
            return jsonify({"ok": False, "error": str(exc)}), 400
        flash(str(exc), "danger")
        return redirect(url_for("maintenance.index"))


@maintenance_bp.route("/google/callback", methods=["GET"])
@login_required
@permission_required("backup.view")
def google_callback():
    error = request.args.get("error")
    if error:
        flash(f"Google sign-in cancelled: {error}", "warning")
        return redirect(url_for("maintenance.index"))
    code = request.args.get("code")
    if not code:
        flash("Google sign-in did not return a code.", "danger")
        return redirect(url_for("maintenance.index"))
    try:
        info = drive.finish_auth_flow(code, state=request.args.get("state"))
        result = drive.process_upload_queue(manual=True)
        if result.get("uploaded"):
            flash(
                f"Google Drive connected as {info.get('email', 'your account')}. "
                f"{result.get('message')}",
                "success",
            )
        else:
            flash(f"Google Drive connected as {info.get('email', 'your account')}.", "success")
            if result.get("remaining"):
                flash(f"{result['remaining']} backup(s) queued — will upload when online.", "info")
    except Exception as exc:
        flash(f"Could not connect Google Drive: {exc}", "danger")
    return redirect(url_for("maintenance.index"))


@maintenance_bp.route("/google/disconnect", methods=["POST"])
@login_required
@permission_required("backup.view")
def google_disconnect():
    drive.disconnect()
    flash("Google Drive disconnected.", "success")
    return redirect(url_for("maintenance.index"))


@maintenance_bp.route("/google/upload", methods=["POST"])
@login_required
@permission_required("backup.view")
def google_upload_now():
    wants_json = request.headers.get("X-Requested-With") == "fetch" or request.form.get("ajax") == "1"
    backup_name = (request.form.get("backup_name") or "").strip()
    try:
        if backup_name:
            folder = resolve_backup_entry(backup_name)
            if not os.path.isdir(folder):
                raise ValueError("Selected backup is not a folder backup.")
            drive.enqueue_backup(os.path.basename(folder), reason="Manual upload requested")
        result = drive.process_upload_queue(manual=True)
        log_audit("drive_upload", "database", None, result.get("message"))
        db.session.commit()
        msg = result.get("message") or "Upload finished."
        if wants_json:
            return jsonify({"ok": result.get("ok", True), "message": msg, **result})
        flash(msg, "success" if result.get("uploaded") else "info")
    except Exception as exc:
        db.session.rollback()
        if wants_json:
            return jsonify({"ok": False, "error": str(exc)}), 400
        flash(str(exc), "danger")
    return redirect(url_for("maintenance.index"))


@maintenance_bp.route("/google/process-queue", methods=["POST"])
@login_required
@permission_required("backup.view")
def google_process_queue():
    wants_json = request.headers.get("X-Requested-With") == "fetch" or request.form.get("ajax") == "1"
    try:
        result = drive.process_upload_queue(manual=True)
        log_audit("drive_queue", "database", None, result.get("message"))
        db.session.commit()
        msg = result.get("message") or "Queue processed."
        if wants_json:
            return jsonify({"ok": result.get("ok", True), "message": msg, **result})
        flash(msg, "success" if result.get("uploaded") else "info")
    except Exception as exc:
        db.session.rollback()
        if wants_json:
            return jsonify({"ok": False, "error": str(exc)}), 400
        flash(str(exc), "danger")
    return redirect(url_for("maintenance.index"))


@maintenance_bp.route("/google/restore", methods=["POST"])
@login_required
@permission_required("backup.view")
def google_restore():
    file_id = (request.form.get("file_id") or "").strip()
    if not file_id:
        flash("Choose a Google Drive backup to restore.", "danger")
        return redirect(url_for("maintenance.index"))
    try:
        # Safety backup before overwrite
        safety = backup_sqlite()
        message = drive.restore_from_drive(file_id)
        log_audit("drive_restore", "database", None, f"{file_id} (safety: {safety})")
        db.session.commit()
        flash(f"{message} A local safety backup was saved first.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("maintenance.index"))


@maintenance_bp.route("/restore/<path:filename>", methods=["POST"])
@login_required
@permission_required("backup.view")
def restore_local(filename: str):
    name = os.path.basename(filename)
    try:
        path = resolve_backup_entry(name)
        safety = backup_sqlite()
        restore_sqlite(path)
        log_audit("restore", "database", None, f"{name} (safety: {safety})")
        db.session.commit()
        flash("Database restored from local backup. A safety backup was saved first.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(str(exc), "danger")
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

        if os.path.isdir(path):
            try:
                from app.services.toast_service import notify_backup_created

                notify_backup_created(app_name, auto=False)
            except Exception:
                pass

        drive_file_id = None
        drive_status = "skipped"
        if os.path.isdir(path):
            drive_status = _upload_latest_backup_to_drive(path)
            drive_file_id = drive_status == "uploaded"

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
                    "drive_uploaded": drive_status == "uploaded",
                    "drive_queued": drive_status == "queued",
                }
            )
        if dest_path:
            flash(f"Backup saved to: {path}", "success")
        elif drive_status == "uploaded":
            flash(
                f"Backup created and uploaded to Google Drive: {app_name}",
                "success",
            )
        elif drive_status == "queued":
            flash(
                f"Backup created locally: {app_name}. Queued for Google Drive (will upload when online).",
                "info",
            )
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

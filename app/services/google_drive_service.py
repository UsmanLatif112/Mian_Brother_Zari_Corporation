"""Google Drive cloud backup for SQLite (upload, list, restore, retention)."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import zipfile
from datetime import datetime
from typing import Any

from flask import current_app, url_for

logger = logging.getLogger(__name__)

DRIVE_FOLDER_NAME = "MBZC ERP Backups"
SCOPES = ["https://www.googleapis.com/auth/drive.file"]
STAMP_RE = re.compile(r"sqlite_backup_(\d{8}_\d{6})")

# Allow HTTP redirect on localhost (desktop / dev only)
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

_queue_lock = threading.Lock()
_google_cache: dict[str, Any] | None = None


def _google():
    """Lazy-load Google libraries so the desktop app starts offline without them."""
    global _google_cache
    if _google_cache is None:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import Flow
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

        _google_cache = {
            "Request": Request,
            "Credentials": Credentials,
            "Flow": Flow,
            "build": build,
            "MediaFileUpload": MediaFileUpload,
            "MediaIoBaseDownload": MediaIoBaseDownload,
        }
    return _google_cache


def _token_path() -> str:
    return os.path.join(current_app.instance_path, "google_drive_token.json")


def _meta_path() -> str:
    return os.path.join(current_app.instance_path, "google_drive_meta.json")


def _queue_path() -> str:
    return os.path.join(current_app.instance_path, "google_drive_upload_queue.json")


def _client_config() -> dict[str, Any]:
    client_id = (current_app.config.get("GOOGLE_DRIVE_CLIENT_ID") or "").strip()
    client_secret = (current_app.config.get("GOOGLE_DRIVE_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        raise ValueError(
            "Google Drive is not configured. Set GOOGLE_DRIVE_CLIENT_ID and "
            "GOOGLE_DRIVE_CLIENT_SECRET in .env (see setup instructions)."
        )
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


def redirect_uri() -> str:
    override = (current_app.config.get("GOOGLE_DRIVE_REDIRECT_URI") or "").strip()
    if override:
        return override
    base = (os.environ.get("APP_BASE_URL") or "").strip().rstrip("/")
    if base:
        return f"{base}/sync-backup/google/callback"
    return url_for("maintenance.google_callback", _external=True)


def is_configured() -> bool:
    return bool(
        (current_app.config.get("GOOGLE_DRIVE_CLIENT_ID") or "").strip()
        and (current_app.config.get("GOOGLE_DRIVE_CLIENT_SECRET") or "").strip()
    )


def _load_meta() -> dict[str, Any]:
    path = _meta_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_meta(data: dict[str, Any]) -> None:
    path = _meta_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def _load_credentials():
    g = _google()
    Credentials = g["Credentials"]
    Request = g["Request"]
    path = _token_path()
    if not os.path.isfile(path):
        return None
    try:
        creds = Credentials.from_authorized_user_file(path, SCOPES)
    except (OSError, ValueError):
        return None
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_credentials(creds)
        except Exception:
            logger.exception("Google Drive token refresh failed")
            return None
    return creds if creds and creds.valid else None


def _save_credentials(creds) -> None:
    path = _token_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(creds.to_json())


def is_connected() -> bool:
    try:
        if not os.path.isfile(_token_path()):
            return False
    except Exception:
        return False
    return _load_credentials() is not None


def connection_info() -> dict[str, Any]:
    meta = _load_meta()
    queue = list_upload_queue()
    return {
        "configured": is_configured(),
        "connected": is_connected(),
        "email": meta.get("email"),
        "folder_name": DRIVE_FOLDER_NAME,
        "last_upload_at": meta.get("last_upload_at"),
        "last_upload_name": meta.get("last_upload_name"),
        "last_error": meta.get("last_error"),
        "queue_count": len(queue),
        "queue": queue,
        "last_queue_run_at": meta.get("last_queue_run_at"),
        "last_queue_result": meta.get("last_queue_result"),
    }


def disconnect() -> None:
    _clear_oauth_pending()
    for path in (_token_path(), _meta_path()):
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass


def _oauth_pending_path() -> str:
    return os.path.join(current_app.instance_path, "google_oauth_pending.json")


def _save_oauth_pending(data: dict[str, Any]) -> None:
    path = _oauth_pending_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def _load_oauth_pending() -> dict[str, Any]:
    path = _oauth_pending_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def _clear_oauth_pending() -> None:
    path = _oauth_pending_path()
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def start_auth_flow() -> str:
    g = _google()
    Flow = g["Flow"]
    uri = redirect_uri()
    flow = Flow.from_client_config(
        _client_config(),
        scopes=SCOPES,
        redirect_uri=uri,
        autogenerate_code_verifier=True,
    )
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    if not flow.code_verifier:
        raise ValueError("OAuth setup failed: missing PKCE code verifier.")
    _save_oauth_pending(
        {
            "state": state,
            "code_verifier": flow.code_verifier,
            "redirect_uri": uri,
        }
    )
    return auth_url


def finish_auth_flow(code: str, state: str | None = None) -> dict[str, Any]:
    g = _google()
    Flow = g["Flow"]
    build = g["build"]
    pending = _load_oauth_pending()
    code_verifier = pending.get("code_verifier")
    if not code_verifier:
        raise ValueError("OAuth session expired. Click Connect Google Drive again.")

    expected_state = pending.get("state")
    if state and expected_state and state != expected_state:
        raise ValueError("OAuth state mismatch. Click Connect Google Drive again.")

    uri = pending.get("redirect_uri") or redirect_uri()
    flow = Flow.from_client_config(
        _client_config(),
        scopes=SCOPES,
        redirect_uri=uri,
        autogenerate_code_verifier=True,
    )
    flow.code_verifier = code_verifier
    flow.fetch_token(code=code)
    _clear_oauth_pending()

    creds = flow.credentials
    _save_credentials(creds)

    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    about = service.about().get(fields="user(emailAddress,displayName)").execute()
    user = about.get("user") or {}
    email = user.get("emailAddress") or user.get("displayName") or "Google account"
    meta = _load_meta()
    meta["email"] = email
    meta.pop("last_error", None)
    _save_meta(meta)
    return {"email": email}


def _drive_service():
    build = _google()["build"]
    creds = _load_credentials()
    if not creds:
        raise ValueError("Google Drive is not connected. Connect your account first.")
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _folder_id(service) -> str | None:
    meta = _load_meta()
    folder_id = meta.get("folder_id")
    if folder_id:
        try:
            service.files().get(fileId=folder_id, fields="id,trashed").execute()
            return folder_id
        except Exception:
            pass

    query = (
        "mimeType='application/vnd.google-apps.folder' "
        f"and name='{DRIVE_FOLDER_NAME}' and trashed=false"
    )
    result = (
        service.files()
        .list(q=query, spaces="drive", fields="files(id,name)", pageSize=1)
        .execute()
    )
    files = result.get("files") or []
    if files:
        folder_id = files[0]["id"]
    else:
        created = (
            service.files()
            .create(
                body={"name": DRIVE_FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"},
                fields="id",
            )
            .execute()
        )
        folder_id = created["id"]

    meta["folder_id"] = folder_id
    _save_meta(meta)
    return folder_id


def _stamp_from_name(name: str) -> str | None:
    match = STAMP_RE.search(name or "")
    return match.group(1) if match else None


def _display_from_stamp(stamp: str | None) -> str:
    if not stamp:
        return "Unknown date"
    try:
        dt = datetime.strptime(stamp, "%Y%m%d_%H%M%S")
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return stamp.replace("_", " ")


def list_drive_backups(limit: int | None = None) -> list[dict[str, Any]]:
    service = _drive_service()
    folder_id = _folder_id(service)
    query = f"'{folder_id}' in parents and trashed=false and name contains 'sqlite_backup_'"
    result = (
        service.files()
        .list(
            q=query,
            spaces="drive",
            fields="files(id,name,createdTime,size,modifiedTime)",
            orderBy="createdTime desc",
            pageSize=100,
        )
        .execute()
    )
    items: list[dict[str, Any]] = []
    for f in result.get("files") or []:
        name = f.get("name") or ""
        if not name.lower().endswith(".zip"):
            continue
        stamp = _stamp_from_name(name)
        items.append(
            {
                "id": f.get("id"),
                "name": name,
                "stamp": stamp,
                "display": _display_from_stamp(stamp),
                "created_time": f.get("createdTime"),
                "size": int(f.get("size") or 0),
            }
        )
    items.sort(key=lambda x: x.get("stamp") or "", reverse=True)
    if limit is not None and limit > 0:
        items = items[:limit]
    return items


def _set_last_upload(name: str) -> None:
    meta = _load_meta()
    meta["last_upload_at"] = datetime.now().isoformat(timespec="seconds")
    meta["last_upload_name"] = name
    meta.pop("last_error", None)
    _save_meta(meta)


def _set_last_error(message: str) -> None:
    meta = _load_meta()
    meta["last_error"] = message[:500]
    _save_meta(meta)


def record_error(message: str) -> None:
    _set_last_error(message)


def _load_queue() -> dict[str, Any]:
    path = _queue_path()
    if not os.path.isfile(path):
        return {"items": []}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {"items": []}
    if not isinstance(data.get("items"), list):
        data["items"] = []
    return data


def _save_queue(data: dict[str, Any]) -> None:
    path = _queue_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def list_upload_queue() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for raw in _load_queue().get("items") or []:
        name = (raw.get("backup_name") or "").strip()
        if not name:
            continue
        stamp = _stamp_from_name(name)
        items.append(
            {
                "backup_name": name,
                "stamp": stamp,
                "display": _display_from_stamp(stamp),
                "queued_at": raw.get("queued_at"),
                "attempts": int(raw.get("attempts") or 0),
                "last_error": raw.get("last_error"),
            }
        )
    items.sort(key=lambda x: x.get("stamp") or "", reverse=True)
    return items


def enqueue_backup(backup_name: str, *, reason: str | None = None) -> bool:
    """Add a local backup folder name to the upload queue (no duplicates)."""
    backup_name = os.path.basename(backup_name.strip())
    if not backup_name.startswith("sqlite_backup_"):
        return False

    data = _load_queue()
    items = data.get("items") or []
    for item in items:
        if item.get("backup_name") == backup_name:
            return False

    items.append(
        {
            "backup_name": backup_name,
            "queued_at": datetime.now().isoformat(timespec="seconds"),
            "attempts": 0,
            "last_error": reason,
        }
    )
    data["items"] = items
    _save_queue(data)
    logger.info("Queued Google Drive upload: %s", backup_name)
    return True


def dequeue_backup(backup_name: str) -> None:
    backup_name = os.path.basename(backup_name.strip())
    data = _load_queue()
    items = [i for i in (data.get("items") or []) if i.get("backup_name") != backup_name]
    data["items"] = items
    _save_queue(data)


def _update_queue_item(backup_name: str, *, attempts: int, last_error: str | None) -> None:
    data = _load_queue()
    for item in data.get("items") or []:
        if item.get("backup_name") == backup_name:
            item["attempts"] = attempts
            item["last_error"] = (last_error or "")[:500] or None
            break
    _save_queue(data)


def _drive_stamps_on_cloud() -> set[str]:
    try:
        return {b["stamp"] for b in list_drive_backups(limit=None) if b.get("stamp")}
    except Exception:
        return set()


def upload_or_queue_folder(folder_path: str) -> str:
    """
    Try immediate Drive upload; on failure or when not connected, queue for later.

    Returns: 'uploaded' | 'queued' | 'skipped'
    """
    if not bool(current_app.config.get("GOOGLE_DRIVE_AUTO_UPLOAD", True)):
        return "skipped"

    backup_name = os.path.basename(str(folder_path).rstrip("\\/"))
    if not os.path.isdir(folder_path):
        return "skipped"

    if not is_connected():
        enqueue_backup(backup_name, reason="Google Drive not connected")
        try:
            from app.services.toast_service import notify_backup_queued

            notify_backup_queued(backup_name, reason="Google Drive not connected")
        except Exception:
            pass
        return "queued"

    try:
        stamp = _stamp_from_name(backup_name)
        if stamp and stamp in _drive_stamps_on_cloud():
            dequeue_backup(backup_name)
            return "uploaded"
        file_id = upload_backup_folder(folder_path)
        if file_id:
            dequeue_backup(backup_name)
            try:
                from app.services.toast_service import notify_backup_uploaded

                zip_name = f"sqlite_backup_{stamp}.zip" if stamp else backup_name
                notify_backup_uploaded(zip_name)
            except Exception:
                pass
            return "uploaded"
    except Exception as exc:
        record_error(str(exc))
        enqueue_backup(backup_name, reason=str(exc))
        try:
            from app.services.toast_service import notify_backup_queued

            notify_backup_queued(backup_name, reason=str(exc))
        except Exception:
            pass
        return "queued"

    enqueue_backup(backup_name, reason="Upload failed")
    try:
        from app.services.toast_service import notify_backup_queued

        notify_backup_queued(backup_name, reason="Upload failed")
    except Exception:
        pass
    return "queued"


def process_upload_queue(*, manual: bool = False) -> dict[str, Any]:
    """
    Upload all queued local backups when Drive is connected and reachable.

    Returns summary: uploaded, skipped, failed, remaining.
    """
    if not _queue_lock.acquire(blocking=False):
        return {"ok": False, "message": "Upload queue is already running.", "uploaded": 0, "failed": 0, "remaining": len(list_upload_queue())}

    try:
        if not bool(current_app.config.get("GOOGLE_DRIVE_AUTO_UPLOAD", True)):
            return {"ok": False, "message": "Google Drive auto-upload is disabled.", "uploaded": 0, "failed": 0, "remaining": 0}

        if not is_connected():
            remaining = len(list_upload_queue())
            return {
                "ok": False,
                "message": "Connect Google Drive first.",
                "uploaded": 0,
                "failed": 0,
                "remaining": remaining,
            }

        from app.services.backup_service import resolve_backup_entry

        queue_items = list(_load_queue().get("items") or [])
        if not queue_items:
            return {"ok": True, "message": "No queued backups.", "uploaded": 0, "failed": 0, "remaining": 0}

        uploaded = 0
        failed = 0
        skipped = 0
        on_cloud = _drive_stamps_on_cloud()

        # Oldest first so cloud history matches creation order
        queue_items.sort(key=lambda x: _stamp_from_name(x.get("backup_name") or "") or "")

        for item in queue_items:
            backup_name = (item.get("backup_name") or "").strip()
            if not backup_name:
                continue
            try:
                folder = resolve_backup_entry(backup_name)
            except FileNotFoundError:
                dequeue_backup(backup_name)
                skipped += 1
                continue

            stamp = _stamp_from_name(backup_name)
            if stamp and stamp in on_cloud:
                dequeue_backup(backup_name)
                skipped += 1
                continue

            attempts = int(item.get("attempts") or 0) + 1
            try:
                file_id = upload_backup_folder(folder)
                if not file_id:
                    raise ValueError("Upload returned no file id")
                dequeue_backup(backup_name)
                uploaded += 1
                if stamp:
                    on_cloud.add(stamp)
            except Exception as exc:
                failed += 1
                err = str(exc)
                record_error(err)
                _update_queue_item(backup_name, attempts=attempts, last_error=err)

        remaining = len(list_upload_queue())
        if uploaded:
            msg = f"Uploaded {uploaded} queued backup(s) to Google Drive."
        elif failed:
            msg = f"Could not upload {failed} queued backup(s). Will retry when online."
        elif skipped:
            msg = "Queued backups were already on Drive or local files were missing."
        else:
            msg = "No queued backups to upload."

        meta = _load_meta()
        meta["last_queue_run_at"] = datetime.now().isoformat(timespec="seconds")
        meta["last_queue_result"] = msg
        if uploaded:
            meta.pop("last_error", None)
        _save_meta(meta)

        try:
            from app.services.toast_service import notify_queue_failed, notify_queue_uploaded

            if uploaded:
                notify_queue_uploaded(uploaded)
            elif manual and failed:
                notify_queue_failed(failed)
        except Exception:
            pass

        return {
            "ok": uploaded > 0 or (failed == 0 and remaining == 0),
            "message": msg,
            "uploaded": uploaded,
            "failed": failed,
            "skipped": skipped,
            "remaining": remaining,
            "manual": manual,
        }
    finally:
        _queue_lock.release()


def upload_backup_zip(zip_path: str, stamp: str | None = None) -> str:
    MediaFileUpload = _google()["MediaFileUpload"]
    service = _drive_service()
    folder_id = _folder_id(service)
    stamp = stamp or _stamp_from_name(os.path.basename(zip_path)) or datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )
    drive_name = f"sqlite_backup_{stamp}.zip"

    media = MediaFileUpload(zip_path, mimetype="application/zip", resumable=True)
    created = (
        service.files()
        .create(
            body={"name": drive_name, "parents": [folder_id]},
            media_body=media,
            fields="id,name",
        )
        .execute()
    )
    _set_last_upload(drive_name)
    keep = int(current_app.config.get("GOOGLE_DRIVE_RETENTION", 10) or 10)
    prune_old_backups(keep=keep)
    return created.get("id") or drive_name


def upload_backup_folder(folder_path: str) -> str | None:
    """Zip a local backup folder and upload to Drive. Returns Drive file id."""
    if not is_connected():
        return None
    from app.services.backup_service import zip_backup_folder

    zip_path = zip_backup_folder(folder_path)
    try:
        return upload_backup_zip(zip_path, stamp=_stamp_from_name(os.path.basename(folder_path)))
    finally:
        try:
            if os.path.isfile(zip_path):
                os.remove(zip_path)
        except OSError:
            pass


def prune_old_backups(keep: int = 10) -> int:
    if keep <= 0:
        return 0
    service = _drive_service()
    backups = list_drive_backups()
    removed = 0
    for entry in backups[keep:]:
        try:
            service.files().delete(fileId=entry["id"]).execute()
            removed += 1
        except Exception:
            logger.exception("Failed to delete old Drive backup %s", entry.get("name"))
    return removed


def download_backup_file(file_id: str, dest_path: str) -> str:
    MediaIoBaseDownload = _google()["MediaIoBaseDownload"]
    service = _drive_service()
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    request = service.files().get_media(fileId=file_id)
    with open(dest_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return dest_path


def restore_from_drive(file_id: str) -> str:
    """Download a Drive backup zip and restore into the live SQLite database."""
    from app.services.backup_service import restore_sqlite

    with tempfile.TemporaryDirectory(prefix="mbzc_drive_restore_") as tmp:
        zip_path = os.path.join(tmp, "restore.zip")
        download_backup_file(file_id, zip_path)
        extract_dir = os.path.join(tmp, "extracted")
        os.makedirs(extract_dir, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)

        # Backup folder may be nested inside the zip
        restore_root = extract_dir
        for name in os.listdir(extract_dir):
            candidate = os.path.join(extract_dir, name)
            if os.path.isdir(candidate) and name.startswith("sqlite_backup_"):
                restore_root = candidate
                break

        restore_sqlite(restore_root)
    return "Database restored from Google Drive backup."

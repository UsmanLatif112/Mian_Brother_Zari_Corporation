"""App-wide toast notifications (persisted for background jobs + polling from any page)."""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta
from typing import Any

from flask import current_app

logger = logging.getLogger(__name__)

_toast_lock = threading.Lock()
_MAX_TOASTS = 100


def _toast_path() -> str:
    return os.path.join(current_app.instance_path, "app_toasts.json")


def _load() -> dict[str, Any]:
    path = _toast_path()
    if not os.path.isfile(path):
        return {"next_id": 1, "items": []}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {"next_id": 1, "items": []}
    if not isinstance(data.get("items"), list):
        data["items"] = []
    if not isinstance(data.get("next_id"), int):
        data["next_id"] = len(data["items"]) + 1
    return data


def _save(data: dict[str, Any]) -> None:
    path = _toast_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def _prune_items(items: list[dict[str, Any]], *, max_age_minutes: int = 120) -> list[dict[str, Any]]:
    cutoff = datetime.now() - timedelta(minutes=max_age_minutes)
    kept: list[dict[str, Any]] = []
    for item in items:
        raw = item.get("created_at") or ""
        try:
            created = datetime.fromisoformat(raw)
        except ValueError:
            created = cutoff
        if created >= cutoff:
            kept.append(item)
    return kept


def push_toast(message: str, category: str = "info", *, source: str = "system") -> int:
    """Queue a toast for all pages. category: success | info | warning | danger."""
    message = (message or "").strip()
    if not message:
        return 0

    cat = category if category in ("success", "info", "warning", "danger") else "info"
    with _toast_lock:
        data = _load()
        toast_id = int(data.get("next_id") or 1)
        data["next_id"] = toast_id + 1
        items = data.get("items") or []
        items.append(
            {
                "id": toast_id,
                "message": message[:500],
                "category": cat,
                "source": source,
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        if len(items) > _MAX_TOASTS:
            items = items[-_MAX_TOASTS:]
        items = _prune_items(items)
        data["items"] = items
        _save(data)
    logger.info("Toast [%s]: %s", cat, message[:120])
    return toast_id


def fetch_toasts(after_id: int = 0) -> list[dict[str, Any]]:
    """Return toasts with id > after_id (for client polling)."""
    with _toast_lock:
        items = [i for i in (_load().get("items") or []) if int(i.get("id") or 0) > after_id]
    items.sort(key=lambda x: int(x.get("id") or 0))
    return items


def notify_backup_created(backup_name: str, *, auto: bool = False) -> None:
    prefix = "Auto backup created" if auto else "Backup created locally"
    push_toast(f"{prefix}: {backup_name}", "success", source="backup")


def notify_backup_uploaded(backup_name: str) -> None:
    push_toast(f"Backup uploaded to Google Drive: {backup_name}", "success", source="drive")


def notify_backup_queued(backup_name: str, *, reason: str | None = None) -> None:
    reason_l = (reason or "").lower()
    if "not connected" in reason_l:
        msg = f"Backup queued (connect Google Drive to upload): {backup_name}"
        cat = "warning"
    elif any(x in reason_l for x in ("network", "internet", "offline", "timeout", "resolve", "connection")):
        msg = f"Backup queued (no internet): {backup_name}"
        cat = "warning"
    else:
        msg = f"Backup queued for Google Drive: {backup_name}"
        cat = "warning"
    push_toast(msg, cat, source="drive")


def notify_queue_uploaded(count: int) -> None:
    if count <= 0:
        return
    word = "backup" if count == 1 else "backups"
    push_toast(f"Uploaded {count} queued {word} to Google Drive.", "success", source="drive")


def notify_queue_failed(count: int) -> None:
    if count <= 0:
        return
    push_toast(
        f"Could not upload {count} queued backup(s). Will retry when online.",
        "warning",
        source="drive",
    )

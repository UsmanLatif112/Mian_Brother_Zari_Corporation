"""Company logo helpers — keep file on disk and bytes in the user row."""

from __future__ import annotations

import logging
from pathlib import Path

from app.utils.uploads import delete_image, upload_root

logger = logging.getLogger(__name__)


def read_upload_bytes(relative_path: str | None) -> bytes | None:
    if not relative_path:
        return None
    rel = relative_path.replace("\\", "/").lstrip("/")
    if ".." in rel or rel.startswith("/"):
        return None
    abs_path = Path(upload_root()) / Path(*rel.split("/"))
    try:
        if abs_path.is_file():
            return abs_path.read_bytes()
    except OSError:
        logger.exception("Could not read logo file %s", abs_path)
    return None


def write_upload_bytes(relative_path: str, data: bytes) -> bool:
    if not relative_path or not data:
        return False
    rel = relative_path.replace("\\", "/").lstrip("/")
    if ".." in rel or rel.startswith("/"):
        return False
    abs_path = Path(upload_root()) / Path(*rel.split("/"))
    try:
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(data)
        return True
    except OSError:
        logger.exception("Could not write logo file %s", abs_path)
        return False


def sync_logo_blob_from_disk(user) -> None:
    """Load company_logo file into company_logo_data (main DB / package DB)."""
    path = getattr(user, "company_logo", None)
    if not path:
        user.company_logo_data = None
        return
    data = read_upload_bytes(path)
    if data:
        user.company_logo_data = data


def materialize_logo_from_blob(user) -> str | None:
    """
    Ensure the logo file exists on disk for serving.
    If the file is missing but DB blob exists, rewrite the file.
    Also loads disk → blob in-memory when path exists but blob empty (caller should commit).
    Returns relative path or None.
    """
    path = getattr(user, "company_logo", None)
    blob = getattr(user, "company_logo_data", None)
    if path and not blob:
        sync_logo_blob_from_disk(user)
        blob = getattr(user, "company_logo_data", None)
    if not path and not blob:
        return None
    if path:
        rel = path.replace("\\", "/").lstrip("/")
        abs_path = Path(upload_root()) / Path(*rel.split("/"))
        if abs_path.is_file():
            return path
        if blob:
            if write_upload_bytes(path, bytes(blob)):
                return path
            return None
        return path
    # Blob without path — unusual; invent logos/ path
    if blob:
        import uuid

        path = f"logos/{uuid.uuid4().hex}.png"
        if write_upload_bytes(path, bytes(blob)):
            user.company_logo = path
            return path
    return None


def clear_user_logo(user) -> None:
    if getattr(user, "company_logo", None):
        delete_image(user.company_logo)
    user.company_logo = None
    user.company_logo_data = None


def set_user_logo_path(user, logo_path: str) -> None:
    """Set path, replace old file if needed, and store bytes in DB."""
    old = getattr(user, "company_logo", None)
    if old and old != logo_path:
        delete_image(old)
    user.company_logo = logo_path
    sync_logo_blob_from_disk(user)

"""Image upload helpers for customer / vendor / sale line photos."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from flask import current_app, url_for
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8 MB per image


def _ext_ok(filename: str) -> bool:
    return Path(filename or "").suffix.lower() in ALLOWED_EXTENSIONS


def upload_root() -> str:
    root = current_app.config["UPLOAD_FOLDER"]
    os.makedirs(root, exist_ok=True)
    return root


def save_image(file_storage: FileStorage | None, subfolder: str) -> str | None:
    """
    Save an uploaded image under UPLOAD_FOLDER/<subfolder>/.
    Returns relative path like 'customers/ab12.jpg', or None if no file.
    """
    if not file_storage or not getattr(file_storage, "filename", None):
        return None
    filename = secure_filename(file_storage.filename)
    if not filename or not _ext_ok(filename):
        raise ValueError("Invalid image type. Use JPG, PNG, WEBP or GIF.")

    # Size check if stream supports seeking
    try:
        file_storage.stream.seek(0, os.SEEK_END)
        size = file_storage.stream.tell()
        file_storage.stream.seek(0)
        if size > MAX_IMAGE_BYTES:
            raise ValueError("Image is too large (max 8 MB).")
    except (OSError, AttributeError):
        pass

    ext = Path(filename).suffix.lower()
    name = f"{uuid.uuid4().hex}{ext}"
    folder = os.path.join(upload_root(), subfolder)
    os.makedirs(folder, exist_ok=True)
    abs_path = os.path.join(folder, name)
    file_storage.save(abs_path)
    return f"{subfolder}/{name}".replace("\\", "/")


def delete_image(relative_path: str | None) -> None:
    if not relative_path:
        return
    # Prevent path traversal
    rel = relative_path.replace("\\", "/").lstrip("/")
    if ".." in rel or rel.startswith("/"):
        return
    abs_path = os.path.join(upload_root(), *rel.split("/"))
    try:
        if os.path.isfile(abs_path):
            os.remove(abs_path)
    except OSError:
        pass


def image_url(relative_path: str | None) -> str | None:
    if not relative_path:
        return None
    rel = relative_path.replace("\\", "/").lstrip("/")
    return url_for("static", filename=f"uploads/{rel}")

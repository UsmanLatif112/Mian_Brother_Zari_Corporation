"""Resolve bundle (read-only) vs user data (writable) paths for desktop / web."""

from __future__ import annotations

import os
import sys


APP_DISPLAY_NAME = "Mian Brother Fertilizers"
APP_FOLDER_NAME = "MianBrotherFertilizers"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> str:
    """PyInstaller extract dir (or project root in development)."""
    if is_frozen():
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def app_home() -> str:
    """Folder that holds the .exe (or project root)."""
    if is_frozen():
        return os.path.dirname(sys.executable)
    return bundle_dir()


def user_data_dir() -> str:
    """Writable data: DB, uploads, backups (next to exe for easy backup)."""
    path = os.path.join(app_home(), "data")
    os.makedirs(path, exist_ok=True)
    return path


def instance_dir() -> str:
    path = os.path.join(user_data_dir(), "instance")
    os.makedirs(path, exist_ok=True)
    return path


def uploads_dir() -> str:
    path = os.path.join(user_data_dir(), "uploads")
    os.makedirs(path, exist_ok=True)
    return path


def backups_dir() -> str:
    path = os.path.join(user_data_dir(), "backups")
    os.makedirs(path, exist_ok=True)
    return path


def templates_dir() -> str:
    return os.path.join(bundle_dir(), "app", "templates")


def static_dir() -> str:
    return os.path.join(bundle_dir(), "app", "static")


def icon_path() -> str | None:
    candidates = [
        os.path.join(bundle_dir(), "app", "static", "img", "app-icon.ico"),
        os.path.join(bundle_dir(), "app", "static", "favicon.ico"),
        os.path.join(app_home(), "app-icon.ico"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None

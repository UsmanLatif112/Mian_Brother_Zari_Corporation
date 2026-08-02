"""Build per-user SQLite data packages (never touches the live admin DB)."""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.extensions import db
from app.models import User
from app.models.user import UserRole
from app.runtime_paths import app_home, user_data_dir, user_packages_dir

logger = logging.getLogger(__name__)

DB_NAME = "mbzc_erp.db"


def package_filename_for(username: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", (username or "user").strip()) or "user"
    return f"{safe}_MBF_data.zip"


def package_path_for(username: str) -> Path:
    return Path(user_packages_dir()) / package_filename_for(username)


def package_exists(username: str) -> bool:
    return package_path_for(username).is_file()


def _find_env_file() -> Path | None:
    candidates = [
        Path(user_data_dir()) / ".env",
        Path(app_home()) / "data" / ".env",
        Path(app_home()) / ".env",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def _copy_user_into_session(session, source: User) -> None:
    """Insert only this staff user into the package DB (no Super Admin, no other data)."""
    from app.models.user import LICENSE_TRIAL, default_trial_expires_at

    role = source.role
    if isinstance(role, str):
        try:
            role = UserRole(role)
        except ValueError:
            role = UserRole.SALES

    license_type = source.license_type or LICENSE_TRIAL
    license_expires_at = source.license_expires_at
    if license_type == LICENSE_TRIAL and not license_expires_at:
        license_expires_at = default_trial_expires_at()

    package_user = User(
        username=source.username,
        email=source.email,
        password_hash=source.password_hash,
        full_name=source.full_name,
        role=role,
        is_active_user=bool(source.is_active_user),
        remote_id=source.remote_id,
        is_registered=False,
        registration_key=source.registration_key,
        registered_at=None,
        device_id=None,
        license_type=license_type,
        license_expires_at=license_expires_at,
        company_name=source.company_name,
        company_logo=source.company_logo,
        company_logo_data=bytes(source.company_logo_data)
        if getattr(source, "company_logo_data", None)
        else None,
        is_deleted=False,
        deleted_at=None,
    )
    session.add(package_user)


def _write_readme(path: Path, username: str) -> None:
    text = f"""Agri Books — User Data Package
=================================

Username: {username}

What's inside
-------------
- Empty SQLite database with ONLY this user's login credentials
- Company name / logo branding for the sidebar
- No Super Admin / admin account
- No customers, sales, inventory, or other business data
- Optional data/.env for MySQL sync / updates

How to install on a staff PC
----------------------------
1. Unzip the main app (MianBrotherFertilizers) if not already installed.
2. Unzip THIS file into the same folder as MianBrotherFertilizers.exe
   so you get:

     MianBrotherFertilizers\\
       MianBrotherFertilizers.exe
       START_HERE.bat
       data\\
         instance\\mbzc_erp.db
         uploads\\  (company logo, if set)
         .env

3. Run START_HERE.bat
4. Login with your username and password.
5. New users have a 7-day trial (full access). After the trial (or earlier),
   register this PC online with your registration key for a lifetime license.

Notes
-----
- This package only contains your credentials database and connection settings.
- It does NOT replace the app .exe (use Check for updates for that).
- Do not overwrite data\\instance on a PC that already has sales data.
"""
    path.write_text(text, encoding="utf-8")


def build_user_data_package(user: User) -> dict[str, Any]:
    """
    Create a fresh empty SQLite DB containing only this user's credentials, then zip it.

    Does not modify the Super Admin live database.
    Does not include admin / Super Admin or any seed business data.
    """
    if not user or not user.username:
        raise ValueError("User is required.")
    if getattr(user, "is_super_admin", lambda: False)():
        raise ValueError("Super Admin does not need a staff data package.")

    # Ensure models are registered on metadata
    import app.models  # noqa: F401

    work = Path(tempfile.mkdtemp(prefix="mbf-user-pkg-"))
    try:
        instance_dir = work / "data" / "instance"
        instance_dir.mkdir(parents=True, exist_ok=True)
        db_path = instance_dir / DB_NAME

        uri = "sqlite:///" + str(db_path).replace("\\", "/")
        engine = create_engine(uri, future=True)
        db.Model.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine, future=True)

        with SessionLocal() as session:
            _copy_user_into_session(session, user)
            session.commit()
        engine.dispose()

        env_src = _find_env_file()
        if env_src:
            shutil.copy2(env_src, work / "data" / ".env")

        # Include company logo file (from disk or DB blob) for staff branding offline
        logo_rel = (user.company_logo or "").replace("\\", "/").lstrip("/")
        logo_blob = getattr(user, "company_logo_data", None)
        if logo_rel and ".." not in logo_rel:
            from flask import current_app

            upload_root = Path(current_app.config["UPLOAD_FOLDER"])
            src_logo = upload_root / Path(*logo_rel.split("/"))
            dest_logo = work / "data" / "uploads" / Path(*logo_rel.split("/"))
            dest_logo.parent.mkdir(parents=True, exist_ok=True)
            if src_logo.is_file():
                shutil.copy2(src_logo, dest_logo)
            elif logo_blob:
                dest_logo.write_bytes(bytes(logo_blob))

        _write_readme(work / "README.txt", user.username)

        out_path = package_path_for(user.username)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.exists():
            out_path.unlink()

        with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, files in os.walk(work):
                for name in files:
                    full = Path(root) / name
                    arc = full.relative_to(work).as_posix()
                    zf.write(full, arcname=arc)

        return {
            "ok": True,
            "zip_path": str(out_path),
            "filename": out_path.name,
            "username": user.username,
        }
    except Exception:
        logger.exception("Failed to build user data package for %s", getattr(user, "username", "?"))
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)


def ensure_user_data_package(user: User, *, force: bool = False) -> dict[str, Any]:
    path = package_path_for(user.username)
    if path.is_file() and not force:
        return {
            "ok": True,
            "zip_path": str(path),
            "filename": path.name,
            "username": user.username,
            "rebuilt": False,
        }
    result = build_user_data_package(user)
    result["rebuilt"] = True
    return result


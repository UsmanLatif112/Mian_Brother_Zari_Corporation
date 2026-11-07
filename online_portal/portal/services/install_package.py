"""
Build a per-user Agri Books install zip:
  - latest published desktop app release
  - SQLite with ONLY this company's user(s), their key, branding
  - optional full business data if Super Admin uploaded a company DB
    (still restricted to that company's portal users / keys)
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from portal.models import Company, PortalUser

logger = logging.getLogger(__name__)

DB_NAME = "mbzc_erp.db"


def _safe_name(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", (text or "user").strip()) or "user"


def _users_ddl() -> str:
    return """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username VARCHAR(80) NOT NULL UNIQUE,
        email VARCHAR(120),
        password_hash VARCHAR(256) NOT NULL,
        full_name VARCHAR(120) NOT NULL,
        role VARCHAR(32) NOT NULL,
        is_active_user BOOLEAN NOT NULL DEFAULT 1,
        last_login_at DATETIME,
        remote_id INTEGER,
        is_registered BOOLEAN NOT NULL DEFAULT 0,
        registration_key VARCHAR(32),
        registered_at DATETIME,
        device_id VARCHAR(64),
        license_type VARCHAR(20),
        license_expires_at DATETIME,
        company_name VARCHAR(200),
        company_logo VARCHAR(255),
        company_logo_data BLOB,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        sync_updated_at DATETIME,
        is_deleted BOOLEAN NOT NULL DEFAULT 0,
        deleted_at DATETIME
    )
    """


def _map_offline_role(portal_role: str) -> str:
    if portal_role == "company_admin":
        return "admin"
    if portal_role == "shop_clerk":
        return "sales"
    return "sales"


def _insert_portal_user(conn: sqlite3.Connection, user: PortalUser, company: Company) -> None:
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    logo_path = company.logo_path
    logo_data = company.logo_data
    conn.execute("DELETE FROM users WHERE LOWER(username) = LOWER(?)", (user.username,))
    conn.execute(
        """
        INSERT INTO users (
            username, email, password_hash, full_name, role, is_active_user,
            remote_id, is_registered, registration_key, registered_at, device_id,
            license_type, license_expires_at, company_name, company_logo, company_logo_data,
            created_at, updated_at, is_deleted
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
        """,
        (
            user.username,
            user.email,
            user.password_hash,
            user.full_name,
            _map_offline_role(user.role),
            1 if user.is_active_user else 0,
            user.id,
            1 if user.is_registered else 0,
            user.registration_key,
            user.registered_at.strftime("%Y-%m-%d %H:%M:%S") if user.registered_at else None,
            user.device_id,
            user.license_type or "lifetime",
            user.license_expires_at.strftime("%Y-%m-%d %H:%M:%S") if user.license_expires_at else None,
            company.name,
            logo_path,
            logo_data,
            now,
            now,
        ),
    )


def _restrict_db_to_company_users(
    db_path: Path,
    company: Company,
    allowed_users: list[PortalUser],
) -> None:
    """
    Keep business tables as uploaded, but users table = only this company's
    portal accounts (with portal keys / passwords / branding).
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(_users_ddl())
        # Drop any other login accounts (super admin leftovers, etc.)
        conn.execute("DELETE FROM users")
        for u in allowed_users:
            if not u.is_active_user:
                continue
            _insert_portal_user(conn, u, company)
        conn.commit()
    finally:
        conn.close()


def _build_empty_user_db(db_path: Path, company: Company, users: list[PortalUser]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(_users_ddl())
        # Minimal settings so app boots
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key VARCHAR(100) NOT NULL UNIQUE,
                value TEXT,
                updated_at DATETIME
            )
            """
        )
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?,?,?)",
            ("company_name", company.name, now),
        )
        for u in users:
            if u.is_active_user:
                _insert_portal_user(conn, u, company)
        conn.commit()
    finally:
        conn.close()


def _find_app_root(extract_dir: Path) -> Path:
    """Locate folder that contains the .exe (handles nested zip layouts)."""
    exes = list(extract_dir.rglob("*.exe"))
    if not exes:
        # folder-only layout
        children = [p for p in extract_dir.iterdir() if p.is_dir()]
        return children[0] if len(children) == 1 else extract_dir
    # Prefer AgriBooks / MianBrotherFertilizers folder
    for exe in exes:
        name = exe.name.lower()
        if "agri" in name or "mianbrother" in name or "fertilizer" in name:
            return exe.parent
    return exes[0].parent


def _write_readme(path: Path, company: Company, username: str, key: str | None) -> None:
    path.write_text(
        f"""Agri Books — Install package
============================

Company: {company.name}
Username: {username}
Registration key: {key or '(see Super Admin)'}

This zip contains:
- Agri Books desktop app
- SQLite database restricted to YOUR company login(s) and key
- Your company name / logo branding

Setup
-----
1. Unzip this folder completely.
2. Run START_HERE.bat (or the .exe).
3. Login with your username and password.
4. If asked to register the PC, use the registration key above.

Important
---------
- Do not share this zip — it contains your license key.
- Data in this package is only for your company account.
""",
        encoding="utf-8",
    )


def build_company_install_zip(
    *,
    company: Company,
    portal_user: PortalUser,
    releases_dir: Path,
    sqlite_packages_dir: Path,
    cache_dir: Path,
    manifest: dict,
) -> Path:
    """
    Returns path to a zip: app + restricted SQLite for this company user.
    Cached per company/user/release version.
    """
    version = str(manifest.get("version") or "0")
    release_file = (manifest.get("filename") or "").strip()
    if not release_file:
        raise ValueError("No desktop app release published yet.")
    release_path = releases_dir / release_file
    if not release_path.is_file():
        raise ValueError("Release zip missing on server.")

    allowed = [
        u
        for u in company.users.order_by(PortalUser.id).all()
        if u.role in ("company_admin", "shop_clerk") and u.is_active_user
    ]
    if not allowed:
        allowed = [portal_user]

    cache_dir.mkdir(parents=True, exist_ok=True)
    out_name = (
        f"AgriBooks-{_safe_name(company.name)}-{_safe_name(portal_user.username)}-v{version}.zip"
    )
    out_path = cache_dir / out_name

    # Invalidate cache if inputs newer than zip
    company_db = sqlite_packages_dir / str(company.id) / DB_NAME
    inputs = [release_path]
    if company_db.is_file():
        inputs.append(company_db)
    if out_path.is_file():
        out_mtime = out_path.stat().st_mtime
        if all(p.stat().st_mtime <= out_mtime for p in inputs):
            return out_path

    work = Path(tempfile.mkdtemp(prefix="agri-install-"))
    try:
        extract_to = work / "extract"
        extract_to.mkdir()
        with zipfile.ZipFile(release_path, "r") as zf:
            zf.extractall(extract_to)

        app_root = _find_app_root(extract_to)
        data_instance = app_root / "data" / "instance"
        data_instance.mkdir(parents=True, exist_ok=True)
        dest_db = data_instance / DB_NAME

        if company_db.is_file():
            shutil.copy2(company_db, dest_db)
            _restrict_db_to_company_users(dest_db, company, allowed)
        else:
            _build_empty_user_db(dest_db, company, allowed)

        # Logo file for offline branding
        if company.logo_data and company.logo_path:
            logo_rel = company.logo_path.replace("\\", "/").lstrip("/")
            if ".." not in logo_rel:
                logo_dest = app_root / "data" / "uploads" / Path(*logo_rel.split("/"))
                logo_dest.parent.mkdir(parents=True, exist_ok=True)
                logo_dest.write_bytes(bytes(company.logo_data))

        _write_readme(
            app_root / "YOUR_ACCOUNT.txt",
            company,
            portal_user.username,
            portal_user.registration_key,
        )

        if out_path.exists():
            out_path.unlink()
        # Zip the app_root folder under a clean top-level name
        top = f"AgriBooks-{_safe_name(company.name)}"
        with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, files in os.walk(app_root):
                for name in files:
                    full = Path(root) / name
                    rel = full.relative_to(app_root).as_posix()
                    zf.write(full, arcname=f"{top}/{rel}")
        return out_path
    finally:
        shutil.rmtree(work, ignore_errors=True)

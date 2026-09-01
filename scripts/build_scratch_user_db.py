"""
Build a fresh scratch SQLite DB for a user: zero business data, same login,
is_registered=True so they can drop mbzc_erp.db into data/instance and sign in.

Usage:
  python scripts/build_scratch_user_db.py --username saeedhanif --out Saeed_New
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DB_NAME = "mbzc_erp.db"
DEFAULT_SOURCE = ROOT / "instance" / DB_NAME


def _find_user_row(source: Path, username: str) -> sqlite3.Row:
    con = sqlite3.connect(source)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    if username:
        row = cur.execute(
            """
            SELECT * FROM users
            WHERE lower(username) = lower(?)
              AND (is_deleted = 0 OR is_deleted IS NULL)
            """,
            (username.strip(),),
        ).fetchone()
    else:
        row = cur.execute(
            """
            SELECT * FROM users
            WHERE (is_deleted = 0 OR is_deleted IS NULL)
              AND lower(username) LIKE '%saeed%'
            ORDER BY id
            LIMIT 1
            """
        ).fetchone()
    con.close()
    if not row:
        hint = username or "saeed*"
        raise SystemExit(f"User not found in {source} (searched: {hint})")
    return row


def _copy_env_and_logo(out_root: Path, user_row: sqlite3.Row) -> None:
    env_candidates = [
        ROOT / "instance" / ".env",
        ROOT / "data" / ".env",
        ROOT / ".env",
    ]
    for src in env_candidates:
        if src.is_file():
            shutil.copy2(src, out_root / "data" / ".env")
            break

    logo_rel = (user_row["company_logo"] or "").replace("\\", "/").lstrip("/") if "company_logo" in user_row.keys() else ""
    if logo_rel and ".." not in logo_rel:
        upload_roots = [
            ROOT / "instance" / "uploads",
            ROOT / "app" / "static" / "uploads",
        ]
        for base in upload_roots:
            src_logo = base / Path(*logo_rel.split("/"))
            if src_logo.is_file():
                dest = out_root / "data" / "uploads" / Path(*logo_rel.split("/"))
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_logo, dest)
                break


def _write_readme(path: Path, username: str) -> None:
    path.write_text(
        f"""Agri Books — Fresh database for {username}
==========================================

What's inside
-------------
- Empty business data (zero sales, stock, customers, etc.)
- Your existing username + password (unchanged)
- is_registered = TRUE (no registration popup)
- Default categories / units seeded for a new shop

Install on desktop app
----------------------
1. Close Mian Brother Fertilizers completely.
2. Backup your current data folder if needed:
     data\\instance\\mbzc_erp.db
3. Copy from this package:
     data\\instance\\mbzc_erp.db
   into the app folder:
     MianBrotherFertilizers\\data\\instance\\mbzc_erp.db
4. If included, copy data\\.env and data\\uploads\\ as well.
5. Run START_HERE.bat and sign in with your usual username/password.

Notes
-----
- Dashboard totals start at zero.
- MySQL sync: cloud registry is unchanged; use Sync registration if needed.
""",
        encoding="utf-8",
    )


def build_scratch_db(
    *,
    source: Path,
    out_dir: Path,
    username: str = "",
) -> dict:
    import app.models  # noqa: F401
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.extensions import db
    from app.models import User
    from app.models.user import LICENSE_LIFETIME, UserRole
    from app.utils.seed import _seed_reference_data
    from app.services.dashboard_service import ensure_customer_type_column
    from app.services.user_registry_service import ensure_user_registration_columns

    user_row = _find_user_row(source, username)
    uname = user_row["username"]

    out_root = out_dir.resolve()
    instance_dir = out_root / "data" / "instance"
    if out_root.exists():
        shutil.rmtree(out_root)
    instance_dir.mkdir(parents=True, exist_ok=True)
    db_path = instance_dir / DB_NAME

    uri = "sqlite:///" + str(db_path).replace("\\", "/")
    engine = create_engine(uri, future=True)
    db.Model.metadata.create_all(engine)

    from flask import Flask
    from config import DevelopmentConfig

    app = Flask(__name__)
    app.config.from_object(DevelopmentConfig)
    app.config["SQLALCHEMY_DATABASE_URI"] = uri
    db.init_app(app)

    with app.app_context():
        ensure_customer_type_column()
        ensure_user_registration_columns()

        role_raw = user_row["role"] or UserRole.ADMIN.value
        try:
            role = UserRole(role_raw)
        except ValueError:
            role = UserRole.ADMIN

        now = datetime.utcnow()
        new_user = User(
            username=user_row["username"],
            email=user_row["email"],
            password_hash=user_row["password_hash"],
            full_name=user_row["full_name"],
            role=role,
            is_active_user=bool(user_row["is_active_user"]),
            remote_id=user_row["remote_id"] if "remote_id" in user_row.keys() else None,
            is_registered=True,
            registration_key=user_row["registration_key"] if "registration_key" in user_row.keys() else None,
            registered_at=now,
            device_id=user_row["device_id"] if "device_id" in user_row.keys() else None,
            license_type=LICENSE_LIFETIME,
            license_expires_at=None,
            company_name=user_row["company_name"] if "company_name" in user_row.keys() else None,
            company_logo=user_row["company_logo"] if "company_logo" in user_row.keys() else None,
            company_logo_data=bytes(user_row["company_logo_data"])
            if "company_logo_data" in user_row.keys() and user_row["company_logo_data"]
            else None,
            is_deleted=False,
        )
        db.session.add(new_user)
        _seed_reference_data()
        db.session.commit()

    engine.dispose()

    _copy_env_and_logo(out_root, user_row)
    _write_readme(out_root / "README.txt", uname)

    return {
        "username": uname,
        "out_dir": str(out_root),
        "db_path": str(db_path),
        "role": role_raw,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build scratch registered DB for a user")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE), help="Source mbzc_erp.db")
    parser.add_argument("--username", default="", help="Username (default: first *saeed* match)")
    parser.add_argument("--out", default="Saeed_New", help="Output folder name under project root")
    args = parser.parse_args()

    source = Path(args.source)
    if not source.is_file():
        raise SystemExit(f"Source DB not found: {source}")

    out_dir = ROOT / args.out
    result = build_scratch_db(source=source, out_dir=out_dir, username=args.username)
    print("Scratch DB created:")
    print(f"  User:     {result['username']} ({result['role']})")
    print(f"  Folder:   {result['out_dir']}")
    print(f"  Database: {result['db_path']}")
    print("  is_registered=True, license=lifetime, zero business data")


if __name__ == "__main__":
    main()

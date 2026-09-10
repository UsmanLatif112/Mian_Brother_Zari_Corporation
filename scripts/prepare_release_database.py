"""
Copy live instance DB + uploads into releases/database, then migrate schema in place.

With --clean (default): wipe all business data and keep only user logins (username/password).

Usage:
  python scripts/prepare_release_database.py
  python scripts/prepare_release_database.py --source instance --dest releases/data
  python scripts/prepare_release_database.py --dest releases/data --no-clean
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.migrate_instance_db import _checkpoint_wal, migrate_instance_db

DB_NAME = "mbzc_erp.db"
UPLOAD_DIRS = (
    ROOT / "app" / "static" / "uploads",
    ROOT / "instance" / "uploads",
)


def _copy_tree_merge(src: Path, dest: Path) -> int:
    """Copy files from src into dest without deleting existing dest files."""
    if not src.is_dir():
        return 0
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    for item in src.rglob("*"):
        if not item.is_file():
            continue
        rel = item.relative_to(src)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(item, target)
            copied += 1
    return copied


def _clear_uploads(dest_uploads: Path) -> None:
    if dest_uploads.is_dir():
        shutil.rmtree(dest_uploads)
    dest_uploads.mkdir(parents=True, exist_ok=True)


def wipe_business_data_keep_users(instance_dir: Path) -> dict:
    """Delete all business rows; keep users (username + password_hash) and re-seed defaults."""
    import app.models  # noqa: F401
    from flask import Flask
    from sqlalchemy import create_engine, text

    from app.extensions import db
    from app.services.dashboard_service import ensure_customer_type_column
    from app.services.user_registry_service import ensure_user_registration_columns
    from app.utils.seed import _seed_reference_data
    from config import DevelopmentConfig

    db_path = instance_dir / DB_NAME
    if not db_path.is_file():
        raise SystemExit(f"Database not found: {db_path}")

    _checkpoint_wal(db_path)
    uri = "sqlite:///" + str(db_path).replace("\\", "/")
    engine = create_engine(uri, future=True)

    app = Flask(__name__)
    app.config.from_object(DevelopmentConfig)
    app.config["SQLALCHEMY_DATABASE_URI"] = uri
    db.init_app(app)

    with app.app_context():
        ensure_customer_type_column()
        ensure_user_registration_columns()

        user_count = db.session.execute(text("SELECT COUNT(*) FROM users")).scalar() or 0
        tables = [
            row[0]
            for row in db.session.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            ).fetchall()
        ]

        db.session.execute(text("PRAGMA foreign_keys=OFF"))
        for table in tables:
            if table == "users":
                continue
            db.session.execute(text(f'DELETE FROM "{table}"'))
        db.session.execute(text("PRAGMA foreign_keys=ON"))
        db.session.flush()

        _seed_reference_data()
        db.session.commit()

    engine.dispose()
    return {"users_kept": int(user_count)}


def prepare_release_database(source_instance: Path, dest_data: Path, *, clean: bool = True) -> dict:
    dest_instance = dest_data / "instance"
    dest_instance.mkdir(parents=True, exist_ok=True)

    src_db = source_instance / DB_NAME
    if not src_db.is_file():
        raise SystemExit(f"Source database not found: {src_db}")

    _checkpoint_wal(src_db)
    dest_db = dest_instance / DB_NAME
    if dest_db.is_file():
        backup_dir = dest_instance / "backup"
        backup_dir.mkdir(parents=True, exist_ok=True)
        from datetime import datetime

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(dest_db, backup_dir / f"{DB_NAME}.before_copy.{stamp}.bak")
        print(f"Backed up existing release DB: {backup_dir / f'{DB_NAME}.before_copy.{stamp}.bak'}")

    shutil.copy2(src_db, dest_db)
    for suffix in ("-wal", "-shm"):
        extra = source_instance / f"{DB_NAME}{suffix}"
        if extra.is_file():
            shutil.copy2(extra, dest_instance / f"{DB_NAME}{suffix}")
    print(f"Copied database: {src_db} -> {dest_db}")

    dest_uploads = dest_data / "uploads"
    if clean:
        _clear_uploads(dest_uploads)
        upload_copied = 0
        print(f"Cleared uploads: {dest_uploads}")
    else:
        upload_copied = 0
        for src_uploads in UPLOAD_DIRS:
            upload_copied += _copy_tree_merge(src_uploads, dest_uploads)
        print(f"Merged uploads into {dest_uploads} ({upload_copied} new files)")

    result = migrate_instance_db(dest_instance)

    if clean:
        wipe_result = wipe_business_data_keep_users(dest_instance)
        result["users_kept"] = wipe_result["users_kept"]
        result["cleaned"] = True
    else:
        result["cleaned"] = False

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare releases/database from live instance")
    parser.add_argument("--source", default="instance", help="Source folder with mbzc_erp.db")
    parser.add_argument("--dest", default="releases/data", help="Release data folder")
    parser.add_argument(
        "--clean",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Wipe business data; keep only user logins (default: on)",
    )
    args = parser.parse_args()

    source = Path(args.source)
    dest = Path(args.dest)
    if not source.is_absolute():
        source = ROOT / source
    if not dest.is_absolute():
        dest = ROOT / dest

    result = prepare_release_database(source, dest, clean=args.clean)
    before = result["counts_before"]
    after = result["counts_after"]
    lost = {k: before[k] for k in before if k in after and before[k] != after[k]}

    print("\nRelease database ready:", dest)
    if result.get("cleaned"):
        print(f"Business data wiped — kept {result.get('users_kept', 0)} user login(s).")
    elif lost:
        print("WARNING row count changed:", lost)
    else:
        print("Row counts unchanged — no data lost.")
    print("Backup:", result["backup"])


if __name__ == "__main__":
    main()

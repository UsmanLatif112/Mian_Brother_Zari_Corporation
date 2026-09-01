"""
Apply schema updates to an existing SQLite instance DB without deleting data.

Adds missing columns/tables using the same helpers the app runs on startup.

Usage:
  python scripts/migrate_instance_db.py
  python scripts/migrate_instance_db.py --instance "instance 85"
  python scripts/migrate_instance_db.py --instance "D:/path/to/folder"
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DB_NAME = "mbzc_erp.db"

KEY_COLUMNS = {
    "customers": ["cnic", "photo", "is_active", "customer_type", "old_book_no", "joined_date"],
    "vendors": ["cnic", "photo", "is_active"],
    "products": ["is_active"],
}


def _backup_db(instance_dir: Path) -> Path:
    src = instance_dir / DB_NAME
    if not src.is_file():
        raise SystemExit(f"Database not found: {src}")
    backup_dir = instance_dir / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = backup_dir / f"{DB_NAME}.{stamp}.bak"
    shutil.copy2(src, dest)
    for suffix in ("-wal", "-shm"):
        extra = instance_dir / f"{DB_NAME}{suffix}"
        if extra.is_file():
            shutil.copy2(extra, backup_dir / f"{DB_NAME}{suffix}.{stamp}.bak")
    return dest


def _checkpoint_wal(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()
    finally:
        conn.close()


def _column_snapshot(db_path: Path) -> dict:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        out: dict = {}
        for table in KEY_COLUMNS:
            cur.execute(f"PRAGMA table_info({table})")
            out[table] = [r[1] for r in cur.fetchall()]
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('customer_photos','vendor_photos')"
        )
        out["_gallery_tables"] = [r[0] for r in cur.fetchall()]
        return out
    finally:
        conn.close()


def _row_counts(db_path: Path) -> dict:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        counts = {}
        for t in ("customers", "vendors", "sales", "products", "users"):
            try:
                cur.execute(f"SELECT COUNT(*) FROM {t}")
                counts[t] = cur.fetchone()[0]
            except Exception:
                counts[t] = "n/a"
        return counts
    finally:
        conn.close()


def migrate_instance_db(instance_dir: Path) -> dict:
    db_path = instance_dir / DB_NAME
    if not db_path.is_file():
        raise SystemExit(f"No database at {db_path}")

    print(f"Migrating: {db_path}")
    _checkpoint_wal(db_path)
    backup = _backup_db(instance_dir)
    print(f"Backup: {backup}")

    before = _column_snapshot(db_path)
    counts_before = _row_counts(db_path)

    import app.models  # noqa: F401
    from flask import Flask
    from sqlalchemy import create_engine

    from app.extensions import db
    from app.services.dashboard_service import ensure_customer_type_column
    from app.services.user_registry_service import ensure_user_registration_columns
    from config import DevelopmentConfig

    uri = "sqlite:///" + str(db_path.resolve()).replace("\\", "/")
    engine = create_engine(uri, future=True)
    db.Model.metadata.create_all(engine)

    app = Flask(__name__)
    app.config.from_object(DevelopmentConfig)
    app.config["SQLALCHEMY_DATABASE_URI"] = uri
    app.config["INSTANCE_PATH"] = str(instance_dir.resolve())
    db.init_app(app)

    with app.app_context():
        print("Running schema helpers...")
        ensure_customer_type_column()
        ensure_user_registration_columns()
        try:
            from app.models.account import AccountAmountTaken, AccountCashSetup

            AccountCashSetup.__table__.create(db.engine, checkfirst=True)
            AccountAmountTaken.__table__.create(db.engine, checkfirst=True)
        except Exception as exc:
            print(f"  ! account tables: {exc}")

    after = _column_snapshot(db_path)
    counts_after = _row_counts(db_path)

    added = {}
    for table, cols in KEY_COLUMNS.items():
        missing_before = [c for c in cols if c not in before.get(table, [])]
        missing_after = [c for c in cols if c not in after.get(table, [])]
        added[table] = [c for c in missing_before if c not in missing_after]

    new_tables = sorted(set(after.get("_gallery_tables", [])) - set(before.get("_gallery_tables", [])))

    return {
        "backup": str(backup),
        "added_columns": added,
        "new_tables": new_tables,
        "counts_before": counts_before,
        "counts_after": counts_after,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate existing instance SQLite DB")
    parser.add_argument(
        "--instance",
        default="instance 85",
        help='Folder containing mbzc_erp.db (default: "instance 85")',
    )
    args = parser.parse_args()
    instance_dir = Path(args.instance)
    if not instance_dir.is_absolute():
        instance_dir = ROOT / instance_dir

    result = migrate_instance_db(instance_dir)
    print("\nDone — existing data preserved.")
    print("New columns:")
    for table, cols in result["added_columns"].items():
        if cols:
            print(f"  {table}: {', '.join(cols)}")
        else:
            print(f"  {table}: already up to date")
    if result["new_tables"]:
        print("New tables:", ", ".join(result["new_tables"]))
    print("Row counts before:", result["counts_before"])
    print("Row counts after: ", result["counts_after"])
    print(f"Backup: {result['backup']}")


if __name__ == "__main__":
    main()

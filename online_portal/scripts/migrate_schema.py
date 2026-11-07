"""
One-time (or rare) schema migrate for Agri Books online portal.

Run manually from the online_portal folder — NEVER from passenger_wsgi /
create_app worker startup (create_all on every LiteSpeed child locks MySQL
and can suspend the cPanel account).

Usage:
  cd online_portal
  python scripts/migrate_schema.py

Note: SQLAlchemy create_all() only creates missing *tables*. New columns on
existing tables are added below via ALTER TABLE.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))

# (table, column, MySQL DDL fragment after ADD COLUMN)
PORTAL_COLUMN_MIGRATIONS: list[tuple[str, str, str]] = [
    ("portal_users", "registration_key", "registration_key VARCHAR(32) NULL"),
    ("portal_users", "is_registered", "is_registered TINYINT(1) NOT NULL DEFAULT 0"),
    ("portal_users", "registered_at", "registered_at DATETIME NULL"),
    ("portal_users", "device_id", "device_id VARCHAR(64) NULL"),
    ("portal_users", "license_type", "license_type VARCHAR(20) NULL"),
    ("portal_users", "license_expires_at", "license_expires_at DATETIME NULL"),
    ("portal_users", "subscription_plan", "subscription_plan VARCHAR(20) NULL"),
    ("portal_companies", "logo_data", "logo_data LONGBLOB NULL"),
    ("portal_companies", "sync_code", "sync_code VARCHAR(32) NULL"),
    ("portal_companies", "last_sync_at", "last_sync_at DATETIME NULL"),
]


def _existing_columns(conn, table: str) -> set[str]:
    from sqlalchemy import inspect

    insp = inspect(conn)
    if table not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def _add_missing_columns(db) -> list[str]:
    from sqlalchemy import text

    added: list[str] = []
    with db.engine.begin() as conn:
        for table, column, ddl in PORTAL_COLUMN_MIGRATIONS:
            cols = _existing_columns(conn, table)
            if not cols:
                continue
            if column in cols:
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))
            added.append(f"{table}.{column}")
    return added


def main() -> int:
    if not (os.environ.get("MYSQL_DATABASE_URI") or "").strip():
        if (os.environ.get("USE_LOCAL_SQLITE") or "").strip().lower() not in {
            "1",
            "true",
            "yes",
            "on",
        }:
            print("ERROR: Set MYSQL_DATABASE_URI or USE_LOCAL_SQLITE=true in .env")
            return 1

    from portal import create_app
    from portal.extensions import db
    import portal.models  # noqa: F401 — register metadata

    app = create_app()
    with app.app_context():
        print("Creating / verifying portal tables (create_all once)…")
        try:
            db.create_all()
        except Exception as exc:
            print("ERROR: migrate failed:")
            print(" ", exc)
            return 2

        print("Adding missing columns on existing tables…")
        try:
            added = _add_missing_columns(db)
        except Exception as exc:
            print("ERROR: column migrate failed:")
            print(" ", exc)
            return 3

        if added:
            print("OK: added columns:", ", ".join(added))
        else:
            print("OK: no new columns needed")

        tables = sorted(db.metadata.tables.keys())
        print("OK: tables ready:", ", ".join(tables) if tables else "(none registered)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

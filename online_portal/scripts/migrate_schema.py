"""
One-time (or rare) schema migrate for Agri Books online portal.

Run manually from the online_portal folder — NEVER from passenger_wsgi /
create_app worker startup (create_all on every LiteSpeed child locks MySQL
and can suspend the cPanel account).

Usage:
  cd online_portal
  python scripts/migrate_schema.py
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))


def main() -> int:
    if not (os.environ.get("MYSQL_DATABASE_URI") or "").strip():
        # Local SQLite portal is fine for migrate too
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
        tables = sorted(db.metadata.tables.keys())
        print("OK: tables ready:", ", ".join(tables) if tables else "(none registered)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Empty all tables in MYSQL_TEST_DATABASE_URI (test cloud DB only)."""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()


def main() -> int:
    uri = os.environ.get("MYSQL_TEST_DATABASE_URI")
    if not uri:
        print("MYSQL_TEST_DATABASE_URI is not set", file=sys.stderr)
        return 1

    engine = create_engine(
        uri,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 15},
    )
    try:
        with engine.begin() as conn:
            db_name = conn.execute(text("SELECT DATABASE()")).scalar()
            print(f"Truncating tables in database: {db_name}")

            conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            rows = conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE'"
                )
            ).fetchall()
            tables = [r[0] for r in rows]
            print(f"Found {len(tables)} table(s)")
            for name in tables:
                conn.execute(text(f"TRUNCATE TABLE `{name}`"))
                print(f"  truncated: {name}")
            conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
        print(f"Done. Emptied {len(tables)} table(s).")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())

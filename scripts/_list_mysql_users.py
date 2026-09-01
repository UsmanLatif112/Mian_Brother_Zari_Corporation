"""Fetch MySQL registry users for fresh DB build."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
load_dotenv(ROOT / "data" / ".env")

from sqlalchemy import create_engine, text

uri = os.environ.get("MYSQL_DATABASE_URI", "").strip()
if not uri:
    print("MYSQL_DATABASE_URI not set")
    sys.exit(1)

eng = create_engine(uri, pool_pre_ping=True)
with eng.connect() as conn:
    rows = conn.execute(
        text(
            """
            SELECT username, role, is_registered, is_active, license_type,
                   registration_key, email, full_name
            FROM erp_user_registry
            ORDER BY username
            """
        )
    ).fetchall()
    print(f"Found {len(rows)} users in MySQL erp_user_registry:")
    for r in rows:
        print(dict(r._mapping))

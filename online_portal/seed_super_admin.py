"""
Create / update Super Admin in the portal MySQL database.

Usage (from online_portal folder):
  python seed_super_admin.py
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
load_dotenv(os.path.join(ROOT, ".env"))

from sqlalchemy import text

from portal import create_app
from portal.extensions import db
from portal.models import PortalRole, PortalUser


def main() -> int:
    username = (os.environ.get("SUPER_ADMIN_USERNAME") or "U. Technologies").strip()
    password = os.environ.get("SUPER_ADMIN_PASSWORD") or ""
    full_name = (os.environ.get("SUPER_ADMIN_FULL_NAME") or username).strip()
    email = (os.environ.get("SUPER_ADMIN_EMAIL") or "admin@udottechnologies.com").strip()

    if not password:
        print("ERROR: Set SUPER_ADMIN_PASSWORD in online_portal/.env")
        return 1
    if not os.environ.get("MYSQL_DATABASE_URI"):
        print("ERROR: Set MYSQL_DATABASE_URI in online_portal/.env")
        return 1

    # Schema must already exist — run scripts/migrate_schema.py first (not on worker start).
    app = create_app()
    with app.app_context():
        try:
            db.session.execute(text("SELECT 1"))
        except Exception as exc:
            print("ERROR: Cannot reach MySQL (or tables missing).")
            print(" ", exc)
            print()
            print("Run once:  python scripts/migrate_schema.py")
            print("Then fix hosting if needed:")
            print("  1) Confirm DB user + password")
            print("  2) Remote MySQL → allow your PC IP (or %)")
            print("  3) User has ALL PRIVILEGES on the database")
            return 2

        user = PortalUser.query.filter(
            db.func.lower(PortalUser.username) == username.lower()
        ).first()
        if user:
            user.full_name = full_name
            user.email = email
            user.role = PortalRole.SUPER_ADMIN.value
            user.is_active_user = True
            user.company_id = None
            user.set_password(password)
            action = "updated"
        else:
            user = PortalUser(
                username=username,
                email=email,
                full_name=full_name,
                role=PortalRole.SUPER_ADMIN.value,
                is_active_user=True,
            )
            user.set_password(password)
            db.session.add(user)
            action = "created"
        db.session.commit()
        print(f"OK: Super Admin {action}: username={username!r}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

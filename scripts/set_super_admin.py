"""Add/update Super Admin in instance/mbzc_erp.db."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from werkzeug.security import generate_password_hash

DB = Path(__file__).resolve().parents[1] / "instance" / "mbzc_erp.db"
USERNAME = "admin"
PASSWORD = "Usman@03360325"
FULL_NAME = "Super Admin"
EMAIL = "admin@mbzc.local"


def main() -> None:
    if not DB.is_file():
        raise SystemExit(f"DB not found: {DB}")

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cols = {r[1] for r in cur.execute("PRAGMA table_info(users)").fetchall()}
    print("columns:", sorted(cols))

    row = cur.execute(
        "SELECT * FROM users WHERE lower(username) = lower(?)",
        (USERNAME,),
    ).fetchone()

    password_hash = generate_password_hash(PASSWORD)

    if row:
        sets = [
            "password_hash = ?",
            "role = ?",
            "is_active_user = 1",
            "is_deleted = 0",
            "deleted_at = NULL",
            "is_registered = 1",
        ]
        params: list = [password_hash, "super_admin"]
        if "license_type" in cols:
            sets.append("license_type = ?")
            params.append("lifetime")
        if "license_expires_at" in cols:
            sets.append("license_expires_at = NULL")
        if "full_name" in cols and not (row["full_name"] or "").strip():
            sets.append("full_name = ?")
            params.append(FULL_NAME)
        if "email" in cols and not (row["email"] or "").strip():
            sets.append("email = ?")
            params.append(EMAIL)
        params.append(row["id"])
        cur.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
        print(f"Updated existing user id={row['id']} username={USERNAME} -> super_admin")
    else:
        # Build insert with available columns
        fields = {
            "username": USERNAME,
            "email": EMAIL,
            "password_hash": password_hash,
            "full_name": FULL_NAME,
            "role": "super_admin",
            "is_active_user": 1,
            "is_deleted": 0,
            "is_registered": 1,
        }
        if "license_type" in cols:
            fields["license_type"] = "lifetime"
        if "created_at" in cols:
            fields["created_at"] = "datetime('now')"
        if "updated_at" in cols:
            fields["updated_at"] = "datetime('now')"

        # Use SQL datetime for timestamp columns
        col_names = []
        placeholders = []
        values = []
        for k, v in fields.items():
            if k not in cols:
                continue
            col_names.append(k)
            if v == "datetime('now')":
                placeholders.append("datetime('now')")
            else:
                placeholders.append("?")
                values.append(v)

        sql = f"INSERT INTO users ({', '.join(col_names)}) VALUES ({', '.join(placeholders)})"
        cur.execute(sql, values)
        conn.commit()
        print(f"Inserted new super_admin username={USERNAME} id={cur.lastrowid}")

    check = cur.execute(
        "SELECT id, username, role, is_active_user, is_deleted, is_registered FROM users WHERE lower(username)=lower(?)",
        (USERNAME,),
    ).fetchone()
    print("result:", dict(check))
    conn.close()


if __name__ == "__main__":
    main()

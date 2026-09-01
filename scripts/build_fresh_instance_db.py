"""
Replace instance/mbzc_erp.db with a fresh database:
- Super Admin (admin) for lost-access recovery
- All users from MySQL erp_user_registry with registration flags
- Zero business data; reference categories/units seeded

Usage:
  python scripts/build_fresh_instance_db.py
  python scripts/build_fresh_instance_db.py --super-password 'YourPass'
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DB_NAME = "mbzc_erp.db"
INSTANCE = ROOT / "instance"


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
        load_dotenv(ROOT / "data" / ".env")
    except ImportError:
        pass


def _backup_db(instance_dir: Path) -> Path | None:
    src = instance_dir / DB_NAME
    if not src.is_file():
        return None
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


def _remove_db(instance_dir: Path) -> None:
    for name in (DB_NAME, f"{DB_NAME}-wal", f"{DB_NAME}-shm"):
        p = instance_dir / name
        if p.is_file():
            p.unlink()


def _fetch_all_mysql_users() -> list[dict]:
    from sqlalchemy import create_engine, text

    from app.services.user_registry_service import REGISTRY_TABLE, ensure_registry_table

    uri = (os.environ.get("MYSQL_DATABASE_URI") or "").strip()
    if not uri:
        raise SystemExit("MYSQL_DATABASE_URI not set in .env — cannot pull users from MySQL.")

    eng = create_engine(uri, pool_pre_ping=True)
    ensure_registry_table(eng)
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT id, username, email, full_name, role, password_hash,
                       registration_key, is_registered, registered_at, device_id,
                       license_type, license_expires_at,
                       company_name, company_logo, company_logo_data, is_active
                FROM {REGISTRY_TABLE}
                ORDER BY username
                """
            )
        ).fetchall()

    out: list[dict] = []
    for row in rows:
        logo_blob = row[14]
        if isinstance(logo_blob, memoryview):
            logo_blob = logo_blob.tobytes()
        elif logo_blob is not None and not isinstance(logo_blob, bytes):
            try:
                logo_blob = bytes(logo_blob)
            except Exception:
                logo_blob = None
        out.append(
            {
                "cloud_id": int(row[0]),
                "username": row[1],
                "email": row[2],
                "full_name": row[3],
                "role": row[4],
                "password_hash": row[5],
                "registration_key": row[6],
                "is_registered": bool(row[7]),
                "registered_at": row[8],
                "device_id": row[9],
                "license_type": row[10],
                "license_expires_at": row[11],
                "company_name": row[12],
                "company_logo": row[13],
                "company_logo_data": logo_blob,
                "is_active": bool(row[15]),
            }
        )
    return out


def build_fresh_instance_db(*, super_password: str = "admin123") -> dict:
    import app.models  # noqa: F401
    from flask import Flask
    from sqlalchemy import create_engine

    from app.extensions import db
    from app.models import User
    from app.models.user import UserRole
    from app.services.dashboard_service import ensure_customer_type_column
    from app.services.user_registry_service import ensure_user_registration_columns
    from app.utils.seed import _seed_reference_data
    from config import DevelopmentConfig

    _load_env()
    instance_dir = INSTANCE
    instance_dir.mkdir(parents=True, exist_ok=True)

    backup = _backup_db(instance_dir)
    _remove_db(instance_dir)

    db_path = instance_dir / DB_NAME
    uri = "sqlite:///" + str(db_path).replace("\\", "/")
    engine = create_engine(uri, future=True)
    db.Model.metadata.create_all(engine)

    app = Flask(__name__)
    app.config.from_object(DevelopmentConfig)
    app.config["SQLALCHEMY_DATABASE_URI"] = uri
    app.config["INSTANCE_PATH"] = str(instance_dir)
    db.init_app(app)

    mysql_rows = _fetch_all_mysql_users()
    created: list[dict] = []

    with app.app_context():
        ensure_customer_type_column()
        ensure_user_registration_columns()

        # Super Admin — local recovery account (not overwritten by MySQL import)
        super_user = User(
            username="admin",
            email="admin@mbzc.local",
            full_name="Super Administrator",
            role=UserRole.SUPER_ADMIN,
            is_active_user=True,
            is_registered=True,
            license_type="lifetime",
            license_expires_at=None,
            is_deleted=False,
        )
        super_user.set_password(super_password)
        db.session.add(super_user)
        db.session.flush()
        created.append(
            {
                "username": "admin",
                "role": "super_admin",
                "source": "local_super_admin",
                "password": super_password,
            }
        )

        for row in mysql_rows:
            uname = (row["username"] or "").strip()
            if not uname:
                continue
            if uname.lower() == "admin":
                continue  # keep local super admin

            try:
                role = UserRole(row["role"])
            except ValueError:
                role = UserRole.ADMIN if (row["role"] or "").lower() == "admin" else UserRole.SALES

            user = User(
                username=uname,
                email=row["email"] or f"{uname}@mbzc.local",
                password_hash=row["password_hash"],
                full_name=row["full_name"] or uname,
                role=role,
                is_active_user=row["is_active"],
                remote_id=row["cloud_id"],
                is_registered=row["is_registered"],
                registration_key=row["registration_key"],
                registered_at=row["registered_at"],
                device_id=row["device_id"],
                license_type=row["license_type"],
                license_expires_at=row["license_expires_at"],
                company_name=row["company_name"],
                company_logo=row["company_logo"],
                company_logo_data=row["company_logo_data"],
                is_deleted=False,
            )
            if role == UserRole.SUPER_ADMIN:
                user.is_registered = True
            db.session.add(user)
            created.append(
                {
                    "username": uname,
                    "role": role.value,
                    "source": "mysql",
                    "is_registered": row["is_registered"],
                    "license_type": row["license_type"],
                    "registration_key": row["registration_key"],
                }
            )

        _seed_reference_data()
        db.session.commit()

    engine.dispose()

    readme = instance_dir / "FRESH_DB_README.txt"
    lines = [
        "Fresh instance database created",
        "===============================",
        f"Created: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "Super Admin (local recovery):",
        f"  Username: admin",
        f"  Password: {super_password}",
        "",
        "Users from MySQL:",
    ]
    for u in created:
        if u.get("source") == "mysql":
            lines.append(
                f"  - {u['username']} ({u['role']}) registered={u.get('is_registered')} "
                f"license={u.get('license_type')} key={u.get('registration_key')}"
            )
    lines.extend(
        [
            "",
            "Business data: empty (zero sales/stock/customers).",
            "Use admin to manage users; cloud MySQL registry unchanged.",
        ]
    )
    if backup:
        lines.insert(3, f"Backup: {backup}")
    readme.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return {
        "db_path": str(db_path),
        "backup": str(backup) if backup else None,
        "users": created,
        "mysql_count": len(mysql_rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Fresh instance DB with Super Admin + MySQL users")
    parser.add_argument(
        "--super-password",
        default="admin123",
        help="Password for local Super Admin user 'admin' (default: admin123)",
    )
    args = parser.parse_args()

    result = build_fresh_instance_db(super_password=args.super_password)
    print("Fresh database created:")
    print(f"  Path:    {result['db_path']}")
    if result["backup"]:
        print(f"  Backup:  {result['backup']}")
    print(f"  MySQL:   {result['mysql_count']} user(s) imported")
    print("")
    print("Login accounts:")
    for u in result["users"]:
        if u.get("source") == "local_super_admin":
            print(f"  [Super Admin] {u['username']} / {u['password']}")
        else:
            print(
                f"  [MySQL]       {u['username']} ({u['role']}) — use your existing password"
            )


if __name__ == "__main__":
    main()

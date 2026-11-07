"""Pull company logo from desktop-synced MySQL tables into portal_companies."""

from __future__ import annotations

from sqlalchemy import text

from portal.extensions import db
from portal.models import Company


def _is_mysql() -> bool:
    try:
        return db.engine.dialect.name == "mysql"
    except Exception:
        return False


def _table_exists(table: str) -> bool:
    if not _is_mysql():
        return False
    row = db.session.execute(
        text(
            """
            SELECT COUNT(*) FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t
            """
        ),
        {"t": table},
    ).scalar()
    return bool(row)


def hydrate_company_logo(company: Company, username: str | None = None) -> bool:
    """
    If portal company has no logo_data, copy from erp_user_registry / users
    (filled by desktop Sync). Returns True if company.logo_data is available.
    """
    if company is None:
        return False
    if company.logo_data:
        return True
    if not _is_mysql():
        return False

    row = None
    if username and _table_exists("erp_user_registry"):
        row = db.session.execute(
            text(
                """
                SELECT company_logo, company_logo_data, company_name
                FROM erp_user_registry
                WHERE LOWER(username) = LOWER(:u)
                  AND company_logo_data IS NOT NULL
                LIMIT 1
                """
            ),
            {"u": username},
        ).fetchone()
    if row is None and username and _table_exists("users"):
        row = db.session.execute(
            text(
                """
                SELECT company_logo, company_logo_data, company_name
                FROM users
                WHERE LOWER(username) = LOWER(:u)
                  AND company_logo_data IS NOT NULL
                  AND (is_deleted = 0 OR is_deleted IS NULL)
                LIMIT 1
                """
            ),
            {"u": username},
        ).fetchone()
    if row is None and company.id and _table_exists("erp_user_registry"):
        row = db.session.execute(
            text(
                """
                SELECT company_logo, company_logo_data, company_name
                FROM erp_user_registry
                WHERE company_id = :cid
                  AND company_logo_data IS NOT NULL
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ),
            {"cid": company.id},
        ).fetchone()

    if not row or not row[1]:
        return False

    logo_path, logo_data, company_name = row[0], row[1], row[2]
    company.logo_data = bytes(logo_data) if not isinstance(logo_data, (bytes, memoryview)) else logo_data
    if logo_path:
        company.logo_path = logo_path
    if company_name and not (company.name or "").strip():
        company.name = company_name
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return bool(company.logo_data)
    return True

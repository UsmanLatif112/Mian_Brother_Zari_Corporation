"""Global working / posting date for data entry (set by admin on dashboard)."""

from __future__ import annotations

from datetime import date, datetime

from app.models.user import User, UserRole

WORKING_DATE_KEY = "working_date"


def _parse_iso(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def get_working_date() -> date:
    """Effective date for new entries; falls back to calendar today."""
    from app.services.settings_service import get_setting

    stored = _parse_iso(get_setting(WORKING_DATE_KEY))
    return stored or date.today()


def is_custom_working_date() -> bool:
    from app.services.settings_service import get_setting

    return bool(_parse_iso(get_setting(WORKING_DATE_KEY)))


def set_working_date(value: date | str | None) -> date:
    """Set global working date, or clear to use calendar today when value is None."""
    from app.services.settings_service import set_setting

    if value is None:
        set_setting(WORKING_DATE_KEY, "")
        return date.today()
    if isinstance(value, str):
        parsed = _parse_iso(value)
        if not parsed:
            raise ValueError("Invalid date format. Use YYYY-MM-DD.")
        value = parsed
    set_setting(WORKING_DATE_KEY, value.isoformat())
    return value


def can_manage_working_date(user: User | None) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    return user.role in (UserRole.SUPER_ADMIN, UserRole.ADMIN)


def working_date_payload() -> dict:
    actual = date.today()
    working = get_working_date()
    return {
        "working_date": working.isoformat(),
        "calendar_today": actual.isoformat(),
        "is_custom": working != actual,
    }

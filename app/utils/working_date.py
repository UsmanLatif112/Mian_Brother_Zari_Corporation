"""Global working / posting date for data entry (set by admin on dashboard)."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.user import User

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


def get_working_datetime() -> datetime:
    """
    Timestamp for stock movements / receipts based on the working date.

    Uses the working calendar day with the current clock time so multiple
    entries on the same working day keep a sensible FIFO order.
    """
    working = get_working_date()
    now = datetime.now()
    return datetime.combine(working, now.time().replace(microsecond=0))


def as_working_datetime(value: date | datetime | str | None) -> datetime:
    """Convert a date/datetime/ISO string to a posting datetime on that day."""
    if value is None:
        return get_working_datetime()
    if isinstance(value, datetime):
        return value.replace(microsecond=0)
    if isinstance(value, str):
        value = _parse_iso(value) or get_working_date()
    now = datetime.now()
    return datetime.combine(value, now.time().replace(microsecond=0))


def default_entry_date() -> date:
    """SQLAlchemy default for business posting dates."""
    return get_working_date()


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
    from app.models.user import UserRole

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

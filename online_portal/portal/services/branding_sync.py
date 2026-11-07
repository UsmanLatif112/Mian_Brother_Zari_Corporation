"""Company branding helpers.

Logos are managed only by Super Admin on the online portal. Desktop Sync no
longer pushes company logos; do not re-import from erp_user_registry/users
(those BLOBs were often truncated at 64KB and broke the admin preview).
"""

from __future__ import annotations

from portal.models import Company


def hydrate_company_logo(company: Company, username: str | None = None) -> bool:
    """No-op: return whether the portal company already has logo bytes."""
    del username  # unused — kept for call-site compatibility
    if company is None:
        return False
    return bool(company.logo_data)

from flask import request

from app.extensions import db
from app.models import AuditLog


def log_audit(action: str, entity_type: str, entity_id=None, details=None):
    from flask import session
    from flask_login import current_user

    user_id = None
    if getattr(current_user, "is_authenticated", False):
        try:
            user_id = int(current_user.get_id())
        except Exception:
            # Detached User after session reset — fall back to login session key
            raw = session.get("_user_id")
            try:
                user_id = int(raw) if raw is not None else None
            except (TypeError, ValueError):
                user_id = None
    entry = AuditLog(
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        details=details,
        ip_address=request.remote_addr if request else None,
    )
    db.session.add(entry)

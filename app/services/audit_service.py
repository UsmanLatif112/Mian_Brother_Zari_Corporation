from flask import request

from app.extensions import db
from app.models import AuditLog


def log_audit(action: str, entity_type: str, entity_id=None, details=None):
    from flask_login import current_user

    user_id = current_user.id if current_user.is_authenticated else None
    entry = AuditLog(
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        details=details,
        ip_address=request.remote_addr if request else None,
    )
    db.session.add(entry)

from datetime import datetime, timezone

from app.extensions import db


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TimestampMixin:
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
    sync_updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)


class SoftDeleteMixin:
    is_deleted = db.Column(db.Boolean, default=False, nullable=False, index=True)
    deleted_at = db.Column(db.DateTime, nullable=True)

    def soft_delete(self):
        self.is_deleted = True
        self.deleted_at = utcnow()


class AgencyMixin:
    """
    Multi-tenant shop scope.
    agency_id = shop Admin user's id (the "agency").
    Staff of that shop share the same agency_id.
    """

    agency_id = db.Column(db.Integer, nullable=True, index=True)

from datetime import datetime

from sqlalchemy import LargeBinary
from sqlalchemy.dialects.mysql import LONGBLOB

from portal.extensions import db


def utcnow():
    return datetime.utcnow()


# MySQL default BLOB is only 64KB — logos need LONGBLOB
LogoBinary = LargeBinary().with_variant(LONGBLOB(), "mysql")


class Company(db.Model):
    __tablename__ = "portal_companies"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False, unique=True)
    # Stable code used by offline app sync + registry
    sync_code = db.Column(db.String(32), nullable=False, unique=True, index=True)
    logo_path = db.Column(db.String(255), nullable=True)
    logo_data = db.Column(LogoBinary, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    notes = db.Column(db.Text, nullable=True)
    last_sync_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    users = db.relationship("PortalUser", back_populates="company", lazy="dynamic")

    def __repr__(self) -> str:
        return f"<Company {self.name}>"

from app.extensions import db
from app.models.mixins import utcnow


class SyncLog(db.Model):
    __tablename__ = "sync_logs"

    id = db.Column(db.Integer, primary_key=True)
    direction = db.Column(db.String(20), nullable=False)  # push / pull
    status = db.Column(db.String(20), nullable=False)  # success / failed / partial
    message = db.Column(db.Text, nullable=True)
    records_synced = db.Column(db.Integer, default=0)
    started_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    completed_at = db.Column(db.DateTime, nullable=True)


class SyncQueue(db.Model):
    __tablename__ = "sync_queue"

    id = db.Column(db.Integer, primary_key=True)
    entity_type = db.Column(db.String(80), nullable=False, index=True)
    entity_id = db.Column(db.Integer, nullable=False)
    operation = db.Column(db.String(20), nullable=False)  # create / update / delete
    payload = db.Column(db.Text, nullable=True)
    attempts = db.Column(db.Integer, default=0)
    last_error = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    next_retry_at = db.Column(db.DateTime, nullable=True)
    is_resolved = db.Column(db.Boolean, default=False, index=True)

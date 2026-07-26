from flask import g, has_request_context

from app.extensions import db
from app.models import Setting


def get_setting(key: str, default=None):
    row = Setting.query.filter_by(key=key).first()
    return row.value if row else default


def set_setting(key: str, value: str):
    row = Setting.query.filter_by(key=key).first()
    if not row:
        row = Setting(key=key)
        db.session.add(row)
    row.value = value
    db.session.commit()
    if has_request_context() and hasattr(g, "_business_info"):
        delattr(g, "_business_info")


def get_business_info():
    if has_request_context() and getattr(g, "_business_info", None) is not None:
        return g._business_info

    keys = [
        "company_name",
        "company_address",
        "company_phone",
        "company_email",
        "currency",
        "date_format",
        "tax_rate",
    ]
    rows = Setting.query.filter(Setting.key.in_(keys)).all()
    by_key = {r.key: (r.value or "") for r in rows}
    info = {k: by_key.get(k, "") for k in keys}
    info["company_name"] = info.get("company_name") or "Mian Brother Fertilizer"
    if has_request_context():
        g._business_info = info
    return info

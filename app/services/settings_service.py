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


def get_business_info():
    keys = [
        "company_name",
        "company_address",
        "company_phone",
        "company_email",
        "currency",
        "date_format",
        "tax_rate",
    ]
    info = {k: get_setting(k, "") for k in keys}
    info["company_name"] = info.get("company_name") or "MBZC ERP"
    return info

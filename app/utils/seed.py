from decimal import Decimal

from app.extensions import db
from app.models import (
    Category,
    ExpenseCategory,
    Unit,
    UnitType,
    User,
)
from app.models.user import UserRole
from app.services.cashbook_service import _get_or_create_balance
from app.services.settings_service import set_setting


DEFAULT_CATEGORIES = [
    "Fertilizer",
    "Crop Seeds",
    "Animal Feed",
    "Building Material",
    "Pesticides",
    "Crops",
    "Chemicals",
    "Miscellaneous",
]

UNIT_PRESETS = {
    "Liquid": [("Liter", "L"), ("Milliliter", "ml")],
    "Powder": [("Kilogram", "KG"), ("Gram", "g")],
    "Solid": [("Bag", "bag"), ("Piece", "pc"), ("Ton", "ton")],
    "Animal Feed": [("Sack", "sack"), ("Kilogram", "KG")],
    "Seeds": [("Packet", "pkt"), ("Bag", "bag"), ("Kilogram", "KG")],
    "Building Material": [("Bag", "bag"), ("Ton", "ton"), ("Cubic Feet", "cft")],
}


def seed_database():
    if not User.query.filter_by(username="admin").first():
        admin = User(
            username="admin",
            email="admin@mbzc.local",
            full_name="System Administrator",
            role=UserRole.ADMIN,
        )
        admin.set_password("admin123")
        db.session.add(admin)

    for name in DEFAULT_CATEGORIES:
        if not Category.query.filter_by(name=name).first():
            db.session.add(Category(name=name))

    for cat_name in ("Office", "Transport", "Utilities", "Salaries", "Miscellaneous"):
        if not ExpenseCategory.query.filter_by(name=cat_name).first():
            db.session.add(ExpenseCategory(name=cat_name))

    for type_name, units in UNIT_PRESETS.items():
        ut = UnitType.query.filter_by(name=type_name).first()
        if not ut:
            ut = UnitType(name=type_name)
            db.session.add(ut)
            db.session.flush()
        for uname, sym in units:
            if not Unit.query.filter_by(unit_type_id=ut.id, name=uname).first():
                db.session.add(Unit(unit_type_id=ut.id, name=uname, symbol=sym))

    _get_or_create_balance("cash")
    _get_or_create_balance("bank")

    set_setting("company_name", "Mian Brother Fertilizer")
    set_setting("currency", "PKR")
    set_setting("date_format", "%d-%m-%Y")
    set_setting("tax_rate", "17")

    db.session.commit()

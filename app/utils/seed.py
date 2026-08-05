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


def _seed_reference_data() -> None:
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


def seed_database(*, create_default_admin: bool = False) -> None:
    """
    Seed reference data and optionally the default Super Admin account.

    Desktop startup must pass create_default_admin=False so an empty database
    (e.g. customers-only import or staff data package) never gets admin/admin123.
    Use `flask init-db` or `flask reset-db` to create the default admin on purpose.
    """
    user_count = User.query.count()
    admin = User.query.filter_by(username="admin").first()

    if user_count == 0:
        if create_default_admin:
            admin = User(
                username="admin",
                email="admin@mbzc.local",
                full_name="System Administrator",
                role=UserRole.SUPER_ADMIN,
                is_registered=True,
            )
            admin.set_password("admin123")
            db.session.add(admin)
            db.session.flush()
            try:
                from app.services.agency_service import stamp_user_agency

                stamp_user_agency(admin)
            except Exception:
                pass
            _seed_reference_data()
            db.session.commit()
        return

    # Staff package: users exist but no admin — leave DB as-is (credentials only).
    if not admin:
        return

    if admin.role != UserRole.SUPER_ADMIN:
        admin.role = UserRole.SUPER_ADMIN
    if not admin.is_registered:
        admin.is_registered = True

    _seed_reference_data()
    db.session.commit()

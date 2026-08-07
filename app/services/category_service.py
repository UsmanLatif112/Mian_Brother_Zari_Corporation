"""Category create / rename helpers (any characters; soft-delete safe)."""

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Category
from app.models.mixins import utcnow


def normalize_category_name(name) -> str:
    """Trim only — allow letters, digits, spaces, symbols (e.g. PGR, NPK-15)."""
    return (name or "").strip()


def _same_parent(row_parent_id, parent_id) -> bool:
    if parent_id in (None, "", 0, "0"):
        return row_parent_id is None
    try:
        return int(row_parent_id or 0) == int(parent_id)
    except (TypeError, ValueError):
        return False


def find_active_category(name: str, parent_id=None, exclude_id=None):
    q = Category.query.filter(
        Category.is_deleted.is_(False),
        Category.name.ilike(name),
    )
    if parent_id in (None, "", 0, "0"):
        q = q.filter(Category.parent_id.is_(None))
    else:
        q = q.filter(Category.parent_id == int(parent_id))
    if exclude_id:
        q = q.filter(Category.id != int(exclude_id))
    return q.first()


def find_deleted_category_by_name(name: str):
    """Any soft-deleted row with this name (blocks unique index on older DBs)."""
    return (
        Category.query.filter(
            Category.is_deleted.is_(True),
            Category.name.ilike(name),
        )
        .order_by(Category.id.desc())
        .first()
    )


def get_or_create_category(name: str, parent_id=None, description=None):
    """
    Create a category/subcategory. Name may be any non-empty text.

    - Reuses active row with same name under the same parent
    - Revives soft-deleted row with that name when present (clears unique conflicts)
    """
    name = normalize_category_name(name)
    if not name:
        raise ValueError("Category name is required.")
    if len(name) > 200:
        raise ValueError("Category name is too long (max 200 characters).")

    if parent_id in (None, "", 0, "0"):
        parent_id = None
    else:
        parent_id = int(parent_id)
        parent = db.session.get(Category, parent_id)
        if not parent or parent.is_deleted:
            raise ValueError("Parent category not found.")

    existing = find_active_category(name, parent_id=parent_id)
    if existing:
        return existing, False

    deleted = find_deleted_category_by_name(name)
    if deleted:
        deleted.is_deleted = False
        deleted.deleted_at = None
        deleted.parent_id = parent_id
        if description is not None:
            deleted.description = description
        deleted.updated_at = utcnow()
        db.session.flush()
        return deleted, True

    cat = Category(name=name, parent_id=parent_id, description=description)
    db.session.add(cat)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        # Legacy unique(name): conflict with active elsewhere — reuse/rename scope
        conflict = Category.query.filter(Category.name.ilike(name)).first()
        if conflict and not conflict.is_deleted and _same_parent(conflict.parent_id, parent_id):
            return conflict, False
        if conflict and conflict.is_deleted:
            conflict.is_deleted = False
            conflict.deleted_at = None
            conflict.parent_id = parent_id
            if description is not None:
                conflict.description = description
            db.session.flush()
            return conflict, True
        if conflict and not conflict.is_deleted:
            # Same name under a different parent — keep legacy unique happy by
            # appending a disambiguator only when the DB still enforces global unique.
            raise ValueError(
                f'Name "{name}" is already used. '
                "Choose a slightly different name, or delete/rename the other category."
            )
        raise

    return cat, True

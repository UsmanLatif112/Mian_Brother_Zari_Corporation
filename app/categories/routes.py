from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy import or_

from app.extensions import db
from app.forms import CategoryForm
from app.models import Category, Product
from app.services.audit_service import log_audit
from app.utils.decorators import permission_required

categories_bp = Blueprint("categories", __name__)


def _norm_name(name):
    from app.services.category_service import normalize_category_name

    return normalize_category_name(name)


def _name_taken(name, exclude_id=None, parent_id=None):
    from app.services.category_service import find_active_category

    return find_active_category(name, parent_id=parent_id, exclude_id=exclude_id) is not None


def _product_count(category_id):
    return (
        Product.query.filter(
            Product.is_deleted.is_(False),
            or_(
                Product.category_id == category_id,
                Product.subcategory_id == category_id,
            ),
        ).count()
    )


def _soft_delete_category(cat):
    """Soft-delete category (and empty child subs for top-level). Raises ValueError if in use."""
    used = _product_count(cat.id)
    if used:
        raise ValueError(f'Cannot delete "{cat.name}" — used by {used} product(s).')

    if cat.parent_id is None:
        children = Category.query.filter_by(parent_id=cat.id, is_deleted=False).all()
        for child in children:
            child_used = _product_count(child.id)
            if child_used:
                raise ValueError(
                    f'Cannot delete "{cat.name}" — subcategory "{child.name}" '
                    f"is used by {child_used} product(s)."
                )
        for child in children:
            child.soft_delete()

    cat.soft_delete()


@categories_bp.route("/")
@login_required
@permission_required("inventory.view")
def index():
    status = (request.args.get("status") or "all").strip().lower()
    if status not in ("all", "with_products", "empty"):
        status = "all"

    cats = (
        Category.query.filter(
            Category.is_deleted.is_(False),
            Category.parent_id.is_(None),
        )
        .order_by(Category.name)
        .all()
    )

    rows = []
    for c in cats:
        sub_count = (
            Category.query.filter_by(parent_id=c.id, is_deleted=False).count()
        )
        product_count = _product_count(c.id)
        if status == "with_products" and product_count <= 0:
            continue
        if status == "empty" and product_count > 0:
            continue
        rows.append(
            {
                "cat": c,
                "sub_count": sub_count,
                "product_count": product_count,
            }
        )

    return render_template(
        "categories/index.html",
        rows=rows,
        form=CategoryForm(),
        open_modal=request.args.get("open_modal") == "1",
        total=len(rows),
        selected_status=status,
    )


@categories_bp.route("/create", methods=["GET", "POST"])
@login_required
@permission_required("inventory.*")
def create():
    if request.method == "GET":
        return redirect(url_for("categories.index", open_modal=1))

    form = CategoryForm()
    if form.validate_on_submit():
        from app.services.category_service import get_or_create_category

        name = _norm_name(form.name.data)
        try:
            cat, created = get_or_create_category(
                name,
                parent_id=None,
                description=(form.description.data or "").strip() or None,
            )
            if created:
                log_audit("create", "category", cat.id, cat.name)
                db.session.commit()
                flash("Category created.", "success")
            else:
                db.session.rollback()
                flash(f'Category "{name}" already exists.', "danger")
                return redirect(url_for("categories.index", open_modal=1))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            return redirect(url_for("categories.index", open_modal=1))
        return redirect(url_for("categories.index"))
    flash("Please fix the form errors.", "danger")
    return redirect(url_for("categories.index", open_modal=1))


@categories_bp.route("/<int:category_id>")
@login_required
@permission_required("inventory.view")
def detail(category_id):
    cat = db.session.get(Category, category_id)
    if not cat or cat.is_deleted or cat.parent_id is not None:
        flash("Category not found.", "danger")
        return redirect(url_for("categories.index"))

    status = (request.args.get("status") or "all").strip().lower()
    if status not in ("all", "with_products", "empty"):
        status = "all"

    subs = (
        Category.query.filter(
            Category.is_deleted.is_(False),
            Category.parent_id == cat.id,
        )
        .order_by(Category.name)
        .all()
    )

    sub_rows = []
    for s in subs:
        product_count = _product_count(s.id)
        if status == "with_products" and product_count <= 0:
            continue
        if status == "empty" and product_count > 0:
            continue
        sub_rows.append(
            {
                "cat": s,
                "product_count": product_count,
            }
        )

    return render_template(
        "categories/detail.html",
        category=cat,
        rows=sub_rows,
        form=CategoryForm(),
        open_modal=request.args.get("open_modal") == "1",
        product_count=_product_count(cat.id),
        sub_count=len(sub_rows),
        selected_status=status,
    )


@categories_bp.route("/<int:category_id>/edit", methods=["POST"])
@login_required
@permission_required("inventory.*")
def edit(category_id):
    cat = db.session.get(Category, category_id)
    if not cat or cat.is_deleted:
        flash("Category not found.", "danger")
        return redirect(url_for("categories.index"))

    name = _norm_name(request.form.get("name"))
    description = (request.form.get("description") or "").strip() or None
    if not name:
        flash("Name is required.", "danger")
    elif _name_taken(name, exclude_id=cat.id, parent_id=cat.parent_id):
        flash(f'Name "{name}" is already used.', "danger")
    else:
        from sqlalchemy.exc import IntegrityError

        cat.name = name
        cat.description = description
        try:
            log_audit("update", "category", cat.id, cat.name)
            db.session.commit()
            flash("Category updated.", "success")
        except IntegrityError:
            db.session.rollback()
            flash(
                f'Name "{name}" conflicts with another category. '
                "Choose a different name.",
                "danger",
            )

    if cat.parent_id:
        return redirect(url_for("categories.detail", category_id=cat.parent_id))
    if request.form.get("return_detail") == "1":
        return redirect(url_for("categories.detail", category_id=cat.id))
    return redirect(url_for("categories.index"))


@categories_bp.route("/<int:category_id>/delete", methods=["POST"])
@login_required
@permission_required("inventory.*")
def delete(category_id):
    cat = db.session.get(Category, category_id)
    if not cat or cat.is_deleted:
        flash("Category not found.", "danger")
        return redirect(url_for("categories.index"))

    parent_id = cat.parent_id
    try:
        _soft_delete_category(cat)
        log_audit("delete", "category", cat.id, cat.name)
        db.session.commit()
        flash(f'"{cat.name}" deleted.', "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "danger")

    if parent_id:
        return redirect(url_for("categories.detail", category_id=parent_id))
    return redirect(url_for("categories.index"))


@categories_bp.route("/<int:category_id>/subcategories/create", methods=["POST"])
@login_required
@permission_required("inventory.*")
def create_subcategory(category_id):
    parent = db.session.get(Category, category_id)
    if not parent or parent.is_deleted or parent.parent_id is not None:
        flash("Category not found.", "danger")
        return redirect(url_for("categories.index"))

    form = CategoryForm()
    if form.validate_on_submit():
        from app.services.category_service import get_or_create_category

        name = _norm_name(form.name.data)
        try:
            sub, created = get_or_create_category(
                name,
                parent_id=parent.id,
                description=(form.description.data or "").strip() or None,
            )
            if created:
                log_audit("create", "category", sub.id, f"{parent.name} / {sub.name}")
                db.session.commit()
                flash("Subcategory created.", "success")
            else:
                db.session.rollback()
                flash(f'Name "{name}" already exists under this category.', "danger")
                return redirect(url_for("categories.detail", category_id=parent.id, open_modal=1))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            return redirect(url_for("categories.detail", category_id=parent.id, open_modal=1))
        return redirect(url_for("categories.detail", category_id=parent.id))

    flash("Please fix the form errors.", "danger")
    return redirect(url_for("categories.detail", category_id=parent.id, open_modal=1))

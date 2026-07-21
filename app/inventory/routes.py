from decimal import Decimal
import json
from datetime import date, datetime

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.forms import ProductForm
from app.models import Category, Product, StockLayer, StockMovement, Vendor
from app.services.audit_service import log_audit
from app.services.fifo_service import (
    adjust_batch,
    reprice_all_remaining,
    reprice_batch,
)
from app.services.purchase_service import record_purchase
from app.utils.decorators import permission_required

inventory_bp = Blueprint("inventory", __name__)


def _parse_optional_date(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    return datetime.strptime(raw, "%Y-%m-%d").date()


def _parse_optional_text(raw):
    raw = (raw or "").strip()
    return raw or None


def _inventory_page(form=None, open_modal=False):
    category_id = request.args.get("category_id", type=int)
    q = Product.query.filter_by(is_deleted=False)
    if category_id:
        q = q.filter(
            db.or_(Product.category_id == category_id, Product.subcategory_id == category_id)
        )
    products = q.order_by(Product.name).all()
    categories = Category.query.filter_by(is_deleted=False, parent_id=None).order_by(Category.name).all()
    if not open_modal:
        open_modal = bool(session.pop("open_inventory_modal", False))
    return render_template(
        "inventory/index.html",
        products=products,
        form=form or ProductForm(),
        open_modal=open_modal,
        categories=categories,
        selected_category=category_id or "",
    )


@inventory_bp.route("/")
@login_required
@permission_required("inventory.view")
def index():
    return _inventory_page()


@inventory_bp.route("/categories")
@login_required
@permission_required("inventory.view")
def categories():
    cats = Category.query.filter_by(is_deleted=False, parent_id=None).all()
    return render_template("inventory/categories.html", categories=cats)


@inventory_bp.route("/products/create", methods=["GET", "POST"])
@login_required
@permission_required("inventory.*")
def create_product():
    if request.method == "GET":
        session["open_inventory_modal"] = True
        return redirect(url_for("inventory.index"))
    form = ProductForm()
    if form.validate_on_submit():
        vendor = db.session.get(Vendor, form.vendor_id.data)
        if not vendor or vendor.is_deleted:
            flash("Vendor is required.", "danger")
            return _inventory_page(form=form, open_modal=True)
        qty = Decimal(str(form.opening_stock.data or 0))
        if qty <= 0:
            flash("Opening stock / purchase quantity is required.", "danger")
            return _inventory_page(form=form, open_modal=True)
        sub_id = form.subcategory_id.data or None
        if not sub_id:
            sub_id = None
        try:
            product = Product(
                name=form.name.data,
                sku=form.sku.data,
                barcode=form.barcode.data,
                brand=form.brand.data,
                category_id=form.category_id.data,
                subcategory_id=sub_id,
                purchase_price=form.purchase_price.data or 0,
                sale_price=form.sale_price.data or 0,
                wholesale_price=form.wholesale_price.data or 0,
                retail_price=form.retail_price.data or 0,
                tax_rate=form.tax_rate.data or 0,
                opening_stock=qty,
                current_stock=Decimal("0"),  # fifo_receive will add stock
                minimum_stock=form.minimum_stock.data or 0,
                description=form.description.data,
            )
            db.session.add(product)
            db.session.flush()
            purchase = record_purchase(
                vendor_id=vendor.id,
                items=[
                    {
                        "product": product,
                        "quantity": qty,
                        "unit_price": product.purchase_price,
                        "sale_price": product.sale_price,
                        "batch_number": form.batch_number.data,
                        "expiry_date": form.expiry_date.data,
                    }
                ],
                user_id=current_user.id,
                invoice_no=form.invoice_no.data,
                purchase_date=date.today(),
                notes=f"Inventory add: {product.name}",
            )
            log_audit("create", "product", product.id, product.name)
            log_audit("create", "purchase", purchase.id, purchase.invoice_no)
            db.session.commit()
            flash(
                f"Product created and purchase {purchase.invoice_no} recorded for {vendor.name}.",
                "success",
            )
            return redirect(url_for("inventory.detail", product_id=product.id))
        except Exception as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            return _inventory_page(form=form, open_modal=True)
    return _inventory_page(form=form, open_modal=True)


@inventory_bp.route("/products/<int:product_id>/edit", methods=["POST"])
@login_required
@permission_required("inventory.*")
def edit_product(product_id):
    product = db.session.get(Product, product_id)
    if not product or product.is_deleted:
        flash("Product not found.", "danger")
        return redirect(url_for("inventory.index"))
    name = (request.form.get("name") or "").strip()
    sku = (request.form.get("sku") or "").strip()
    if not name or not sku:
        flash("Name and SKU are required.", "danger")
        return redirect(url_for("inventory.detail", product_id=product_id))
    try:
        product.name = name
        product.sku = sku
        product.barcode = (request.form.get("barcode") or "").strip() or None
        product.brand = (request.form.get("brand") or "").strip() or None
        cat = request.form.get("category_id")
        if cat:
            product.category_id = int(cat)
        sub = request.form.get("subcategory_id")
        product.subcategory_id = int(sub) if sub and str(sub) not in ("", "0") else None
        product.purchase_price = Decimal(request.form.get("purchase_price") or 0)
        product.sale_price = Decimal(request.form.get("sale_price") or 0)
        product.minimum_stock = Decimal(request.form.get("minimum_stock") or 0)
        product.description = (request.form.get("description") or "").strip() or None
        log_audit("update", "product", product.id, product.name)
        db.session.commit()
        flash("Product updated.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(request.form.get("next") or url_for("inventory.detail", product_id=product.id))


@inventory_bp.route("/products/<int:product_id>")
@login_required
@permission_required("inventory.view")
def detail(product_id):
    from datetime import date

    product = db.session.get(Product, product_id)
    if not product or product.is_deleted:
        flash("Product not found.", "danger")
        return redirect(url_for("inventory.index"))
    layers = (
        StockLayer.query.filter_by(product_id=product.id)
        .order_by(StockLayer.received_at.desc(), StockLayer.id.desc())
        .all()
    )
    movements = (
        StockMovement.query.filter_by(product_id=product.id)
        .order_by(StockMovement.created_at.desc())
        .limit(50)
        .all()
    )
    open_batch_count = sum(1 for L in layers if (L.quantity_remaining or 0) > 0)
    form = ProductForm(obj=product)
    form.category_id.data = product.category_id
    form.subcategory_id.data = product.subcategory_id
    return render_template(
        "inventory/detail.html",
        product=product,
        layers=layers,
        movements=movements,
        open_batch_count=open_batch_count,
        form=form,
        today=date.today(),
        categories=Category.query.filter_by(is_deleted=False, parent_id=None).order_by(Category.name).all(),
    )


@inventory_bp.route("/batches/<int:layer_id>/adjust", methods=["POST"])
@login_required
@permission_required("inventory.*")
def adjust_layer(layer_id):
    layer = db.session.get(StockLayer, layer_id)
    if not layer:
        flash("Batch not found.", "danger")
        return redirect(url_for("inventory.index"))
    try:
        new_qty = Decimal(request.form.get("quantity") or 0)
        notes = request.form.get("notes")
        layer.batch_number = _parse_optional_text(request.form.get("batch_number"))
        layer.expiry_date = _parse_optional_date(request.form.get("expiry_date"))
        adjust_batch(layer, new_qty, current_user.id, notes=notes)
        log_audit(
            "adjust",
            "stock_layer",
            layer.id,
            json.dumps(
                {
                    "product_id": layer.product_id,
                    "new_qty": float(new_qty),
                    "batch_number": layer.batch_number,
                    "expiry_date": layer.expiry_date.isoformat() if layer.expiry_date else None,
                    "notes": notes,
                }
            ),
        )
        db.session.commit()
        flash("Batch quantity updated (remaining stock only).", "success")
    except Exception as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("inventory.detail", product_id=layer.product_id))


@inventory_bp.route("/batches/<int:layer_id>/details", methods=["POST"])
@login_required
@permission_required("inventory.*")
def update_layer_details(layer_id):
    """Update optional batch number / expiry / vendor / invoice without changing quantity."""
    layer = db.session.get(StockLayer, layer_id)
    if not layer:
        flash("Batch not found.", "danger")
        return redirect(url_for("inventory.index"))
    try:
        vendor_raw = (request.form.get("vendor_id") or "").strip()
        if not vendor_raw:
            raise ValueError("Vendor is required.")
        vendor = db.session.get(Vendor, int(vendor_raw))
        if not vendor or vendor.is_deleted:
            raise ValueError("Vendor is required.")
        layer.vendor_id = vendor.id
        layer.invoice_no = _parse_optional_text(request.form.get("invoice_no"))
        layer.batch_number = _parse_optional_text(request.form.get("batch_number"))
        layer.expiry_date = _parse_optional_date(request.form.get("expiry_date"))
        log_audit(
            "update",
            "stock_layer",
            layer.id,
            json.dumps(
                {
                    "product_id": layer.product_id,
                    "vendor_id": layer.vendor_id,
                    "invoice_no": layer.invoice_no,
                    "batch_number": layer.batch_number,
                    "expiry_date": layer.expiry_date.isoformat() if layer.expiry_date else None,
                }
            ),
        )
        db.session.commit()
        flash("Batch details updated.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("inventory.detail", product_id=layer.product_id))


@inventory_bp.route("/batches/<int:layer_id>/reprice", methods=["POST"])
@login_required
@permission_required("inventory.*")
def reprice_layer(layer_id):
    layer = db.session.get(StockLayer, layer_id)
    if not layer:
        flash("Batch not found.", "danger")
        return redirect(url_for("inventory.index"))
    try:
        cost_raw = request.form.get("unit_cost")
        sale_raw = request.form.get("sale_price")
        unit_cost = Decimal(cost_raw) if cost_raw not in (None, "") else None
        sale_price = Decimal(sale_raw) if sale_raw not in (None, "") else None
        notes = request.form.get("notes")
        reprice_batch(
            layer,
            unit_cost=unit_cost,
            sale_price=sale_price,
            user_id=current_user.id,
            notes=notes,
        )
        log_audit(
            "reprice",
            "stock_layer",
            layer.id,
            json.dumps(
                {
                    "product_id": layer.product_id,
                    "unit_cost": float(unit_cost) if unit_cost is not None else None,
                    "sale_price": float(sale_price) if sale_price is not None else None,
                    "remaining": float(layer.quantity_remaining or 0),
                    "notes": notes,
                }
            ),
        )
        db.session.commit()
        flash("Batch pricing updated for remaining stock only (sold history unchanged).", "success")
    except Exception as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("inventory.detail", product_id=layer.product_id))


@inventory_bp.route("/products/<int:product_id>/reprice-remaining", methods=["POST"])
@login_required
@permission_required("inventory.*")
def reprice_product_remaining(product_id):
    product = db.session.get(Product, product_id)
    if not product or product.is_deleted:
        flash("Product not found.", "danger")
        return redirect(url_for("inventory.index"))
    try:
        cost_raw = request.form.get("unit_cost")
        sale_raw = request.form.get("sale_price")
        unit_cost = Decimal(cost_raw) if cost_raw not in (None, "") else None
        sale_price = Decimal(sale_raw) if sale_raw not in (None, "") else None
        notes = request.form.get("notes")
        count = reprice_all_remaining(
            product,
            unit_cost=unit_cost,
            sale_price=sale_price,
            user_id=current_user.id,
            notes=notes,
        )
        log_audit(
            "reprice",
            "product",
            product.id,
            json.dumps(
                {
                    "batches": count,
                    "unit_cost": float(unit_cost) if unit_cost is not None else None,
                    "sale_price": float(sale_price) if sale_price is not None else None,
                }
            ),
        )
        db.session.commit()
        flash(f"Updated pricing on {count} open batch(es). Sold stock not affected.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("inventory.detail", product_id=product_id))


@inventory_bp.route("/batches/<int:layer_id>/delete", methods=["POST"])
@login_required
@permission_required("inventory.*")
def delete_layer(layer_id):
    layer = db.session.get(StockLayer, layer_id)
    if not layer:
        flash("Batch not found.", "danger")
        return redirect(url_for("inventory.index"))
    product_id = layer.product_id
    try:
        remaining = Decimal(str(layer.quantity_remaining or 0))
        if remaining > 0:
            adjust_batch(layer, Decimal("0"), current_user.id, notes="Batch cleared / deleted")
        db.session.delete(layer)
        log_audit("delete", "stock_layer", layer_id, f"product={product_id}")
        db.session.commit()
        flash("Batch deleted.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("inventory.detail", product_id=product_id))


@inventory_bp.route("/products/<int:product_id>/delete", methods=["POST"])
@login_required
@permission_required("inventory.*")
def delete_product(product_id):
    product = db.session.get(Product, product_id)
    if not product or product.is_deleted:
        flash("Product not found.", "danger")
        return redirect(url_for("inventory.index"))
    try:
        # Soft-delete; clear remaining stock on open layers for consistency
        open_layers = (
            StockLayer.query.filter_by(product_id=product.id)
            .filter(StockLayer.quantity_remaining > 0)
            .all()
        )
        for layer in open_layers:
            adjust_batch(layer, Decimal("0"), current_user.id, notes="Product deleted — stock cleared")
        product.is_deleted = True
        product.current_stock = Decimal("0")
        log_audit("delete", "product", product.id, product.name)
        db.session.commit()
        flash("Product deleted (soft). History kept; stock cleared.", "success")
        return redirect(url_for("inventory.index"))
    except Exception as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("inventory.detail", product_id=product_id))

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
from app.services.purchase_service import amend_purchase_for_layer, record_purchase
from app.utils.working_date import get_working_date
from app.utils.decorators import permission_required
from app.utils.uploads import accept_uploaded_path, delete_image, save_image

inventory_bp = Blueprint("inventory", __name__)


def _apply_product_photo(product):
    """Optional product photo from multipart form or ajax path."""
    if request.form.get("clear_photo") == "1":
        delete_image(product.photo)
        product.photo = None
        return
    path = accept_uploaded_path(request.form.get("photo_path"), "products")
    if path:
        if product.photo and product.photo != path:
            delete_image(product.photo)
        product.photo = path
        return
    try:
        uploaded = save_image(request.files.get("photo"), "products")
    except ValueError:
        uploaded = None
    if uploaded:
        delete_image(product.photo)
        product.photo = uploaded


def _parse_optional_date(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    return datetime.strptime(raw, "%Y-%m-%d").date()


def _parse_optional_text(raw):
    raw = (raw or "").strip()
    return raw or None


def _inventory_page(form=None, open_modal=False):
    from datetime import time

    from app.services.dashboard_service import _range_for_filter

    category_id = request.args.get("category_id", type=int)
    period = request.args.get("period", "all")
    start_raw = request.args.get("start_date") or ""
    end_raw = request.args.get("end_date") or ""
    try:
        period_start = date.fromisoformat(start_raw) if start_raw else None
    except ValueError:
        period_start = None
    try:
        period_end = date.fromisoformat(end_raw) if end_raw else None
    except ValueError:
        period_end = None
    range_start, range_end = _range_for_filter(period, period_start, period_end)

    q = Product.query.filter_by(is_deleted=False)
    if category_id:
        q = q.filter(
            db.or_(Product.category_id == category_id, Product.subcategory_id == category_id)
        )
    if range_start and range_end:
        start_dt = datetime.combine(range_start, time.min)
        end_dt = datetime.combine(range_end, time.max)
        product_ids = (
            db.session.query(StockLayer.product_id)
            .filter(
                StockLayer.received_at >= start_dt,
                StockLayer.received_at <= end_dt,
            )
            .distinct()
        )
        q = q.filter(Product.id.in_(product_ids))

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
        selected_period=period,
        start_date=start_raw,
        end_date=end_raw,
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
    return redirect(url_for("categories.index"))


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
            from app.utils.product_codes import ensure_product_codes

            existing_id = form.existing_product_id.data
            if existing_id:
                product = db.session.get(Product, int(existing_id))
                if not product or product.is_deleted:
                    flash("Selected product not found.", "danger")
                    return _inventory_page(form=form, open_modal=True)
                product.name = form.name.data
                # Keep existing SKU; allow barcode update (auto only if blank on create)
                barcode_val = (form.barcode.data or "").strip() or product.barcode
                product.barcode = barcode_val
                product.brand = form.brand.data
                product.category_id = form.category_id.data
                product.subcategory_id = sub_id
                product.purchase_price = form.purchase_price.data or 0
                product.sale_price = form.sale_price.data or 0
                product.minimum_stock = form.minimum_stock.data or 0
                product.description = form.description.data
                _apply_product_photo(product)
                action = "update"
            else:
                sku_val, barcode_val = ensure_product_codes(form.sku.data, form.barcode.data)
                if Product.query.filter_by(sku=sku_val).first():
                    flash(f"SKU '{sku_val}' already exists. Clear and use a different SKU.", "danger")
                    return _inventory_page(form=form, open_modal=True)
                if barcode_val and Product.query.filter_by(barcode=barcode_val).first():
                    flash(f"Barcode '{barcode_val}' already exists. Clear and use a different barcode.", "danger")
                    return _inventory_page(form=form, open_modal=True)
                product = Product(
                    name=form.name.data,
                    sku=sku_val,
                    barcode=barcode_val,
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
                _apply_product_photo(product)
                action = "create"

            purchase = record_purchase(
                vendor_id=vendor.id,
                items=[
                    {
                        "product": product,
                        "quantity": qty,
                        "unit_price": form.purchase_price.data or product.purchase_price,
                        "sale_price": form.sale_price.data or product.sale_price,
                        "batch_number": form.batch_number.data,
                        "expiry_date": form.expiry_date.data,
                    }
                ],
                user_id=current_user.id,
                invoice_no=form.invoice_no.data,
                purchase_date=get_working_date(),
                notes=f"Inventory add: {product.name}",
            )
            log_audit(action, "product", product.id, product.name)
            log_audit("create", "purchase", purchase.id, purchase.invoice_no)
            db.session.commit()
            if existing_id:
                flash(
                    f"Stock added to {product.name}. Purchase {purchase.invoice_no} recorded for {vendor.name}.",
                    "success",
                )
            else:
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
        _apply_product_photo(product)
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
    from datetime import date, datetime, time

    from app.services.dashboard_service import _range_for_filter

    product = db.session.get(Product, product_id)
    if not product or product.is_deleted:
        flash("Product not found.", "danger")
        return redirect(url_for("inventory.index"))

    period = request.args.get("period", "all")
    start_raw = request.args.get("start_date") or ""
    end_raw = request.args.get("end_date") or ""
    period_start = date.fromisoformat(start_raw) if start_raw else None
    period_end = date.fromisoformat(end_raw) if end_raw else None
    range_start, range_end = _range_for_filter(period, period_start, period_end)

    layers_q = StockLayer.query.filter_by(product_id=product.id)
    movements_q = StockMovement.query.filter_by(product_id=product.id)
    if range_start and range_end:
        start_dt = datetime.combine(range_start, time.min)
        end_dt = datetime.combine(range_end, time.max)
        layers_q = layers_q.filter(
            StockLayer.received_at >= start_dt,
            StockLayer.received_at <= end_dt,
        )
        movements_q = movements_q.filter(
            StockMovement.created_at >= start_dt,
            StockMovement.created_at <= end_dt,
        )

    layers = layers_q.order_by(StockLayer.received_at.desc(), StockLayer.id.desc()).all()
    movements = (
        movements_q.order_by(StockMovement.created_at.desc()).limit(100).all()
    )
    open_batch_count = sum(1 for L in layers if (L.quantity_remaining or 0) > 0)
    closed_batch_count = sum(1 for L in layers if (L.quantity_remaining or 0) <= 0)
    total_stock = sum((L.quantity_remaining or 0) for L in layers)
    total_stock_value = sum(
        (L.quantity_remaining or 0) * (L.unit_cost or 0)
        for L in layers
        if (L.quantity_remaining or 0) > 0
    )
    form = ProductForm(obj=product)
    form.category_id.data = product.category_id
    form.subcategory_id.data = product.subcategory_id
    return render_template(
        "inventory/detail.html",
        product=product,
        layers=layers,
        movements=movements,
        total_stock=total_stock,
        total_stock_value=total_stock_value,
        open_batch_count=open_batch_count,
        closed_batch_count=closed_batch_count,
        form=form,
        today=date.today(),
        selected_period=period,
        start_date=start_raw,
        end_date=end_raw,
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


@inventory_bp.route("/batches/<int:layer_id>/edit", methods=["POST"])
@login_required
@permission_required("inventory.*")
def edit_layer_entry(layer_id):
    """Edit a stock batch using the same fields as Add Product."""
    layer = db.session.get(StockLayer, layer_id)
    if not layer:
        flash("Batch not found.", "danger")
        return redirect(url_for("inventory.index"))
    product = db.session.get(Product, layer.product_id)
    if not product or product.is_deleted:
        flash("Product not found.", "danger")
        return redirect(url_for("inventory.index"))

    name = (request.form.get("name") or "").strip()
    sku = (request.form.get("sku") or "").strip()
    if not name or not sku:
        flash("Name and SKU are required.", "danger")
        return redirect(url_for("inventory.detail", product_id=product.id))

    try:
        vendor_raw = (request.form.get("vendor_id") or "").strip()
        if not vendor_raw:
            raise ValueError("Vendor is required.")
        vendor = db.session.get(Vendor, int(vendor_raw))
        if not vendor or vendor.is_deleted:
            raise ValueError("Vendor is required.")

        product.name = name
        product.sku = sku
        product.barcode = (request.form.get("barcode") or "").strip() or None
        product.brand = (request.form.get("brand") or "").strip() or None
        cat = request.form.get("category_id")
        if cat:
            product.category_id = int(cat)
        sub = request.form.get("subcategory_id")
        product.subcategory_id = int(sub) if sub and str(sub) not in ("", "0") else None
        product.minimum_stock = Decimal(request.form.get("minimum_stock") or 0)
        product.description = (request.form.get("description") or "").strip() or None
        _apply_product_photo(product)

        purchase_price = Decimal(request.form.get("purchase_price") or 0)
        sale_price = Decimal(request.form.get("sale_price") or 0)
        product.purchase_price = purchase_price
        product.sale_price = sale_price

        old_purchased = Decimal(str(layer.quantity_received or 0))
        old_unit_cost = Decimal(str(layer.unit_cost or 0))
        old_vendor_id = layer.vendor_id

        layer.vendor_id = vendor.id
        layer.invoice_no = _parse_optional_text(request.form.get("invoice_no"))
        layer.batch_number = _parse_optional_text(request.form.get("batch_number"))
        layer.expiry_date = _parse_optional_date(request.form.get("expiry_date"))

        new_purchased = Decimal(
            request.form.get("quantity_received")
            or request.form.get("purchased_qty")
            or 0
        )
        new_qty = Decimal(request.form.get("opening_stock") or request.form.get("quantity") or 0)
        if new_purchased < 0 or new_qty < 0:
            raise ValueError("Quantities cannot be negative.")
        if new_qty > new_purchased:
            raise ValueError("Available qty cannot be more than purchased qty.")

        layer.quantity_received = new_purchased
        current_qty = Decimal(str(layer.quantity_remaining or 0))
        if new_qty != current_qty:
            adj_notes = (request.form.get("adjustment_notes") or "").strip() or "Edited batch quantity"
            adjust_batch(
                layer,
                new_qty,
                current_user.id,
                notes=adj_notes,
            )
        layer.quantity_received = new_purchased

        if Decimal(str(layer.quantity_remaining or 0)) > 0:
            reprice_batch(
                layer,
                unit_cost=purchase_price,
                sale_price=sale_price,
                user_id=current_user.id,
                notes="Edited inventory entry",
            )
        else:
            layer.unit_cost = purchase_price
            layer.sale_price = sale_price

        # Keep linked purchase + vendor payable / ledger in sync with this batch
        amend_purchase_for_layer(
            layer,
            old_purchased=old_purchased,
            new_purchased=new_purchased,
            old_unit_cost=old_unit_cost,
            new_unit_cost=purchase_price,
            new_vendor_id=vendor.id,
            new_invoice_no=layer.invoice_no,
        )

        log_audit(
            "update",
            "stock_layer",
            layer.id,
            json.dumps(
                {
                    "product_id": product.id,
                    "name": product.name,
                    "sku": product.sku,
                    "vendor_id": layer.vendor_id,
                    "invoice_no": layer.invoice_no,
                    "quantity": float(layer.quantity_remaining or 0),
                    "quantity_received": float(layer.quantity_received or 0),
                    "unit_cost": float(layer.unit_cost or 0),
                    "sale_price": float(layer.sale_price or 0) if layer.sale_price is not None else None,
                    "batch_number": layer.batch_number,
                    "expiry_date": layer.expiry_date.isoformat() if layer.expiry_date else None,
                }
            ),
        )
        db.session.commit()
        flash("Inventory entry updated (purchase & vendor payable synced).", "success")
    except Exception as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("inventory.detail", product_id=product.id))


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
        old_purchased = Decimal(str(layer.quantity_received or 0))
        old_unit_cost = Decimal(str(layer.unit_cost or 0))
        layer.vendor_id = vendor.id
        layer.invoice_no = _parse_optional_text(request.form.get("invoice_no"))
        layer.batch_number = _parse_optional_text(request.form.get("batch_number"))
        layer.expiry_date = _parse_optional_date(request.form.get("expiry_date"))
        amend_purchase_for_layer(
            layer,
            old_purchased=old_purchased,
            new_purchased=old_purchased,
            old_unit_cost=old_unit_cost,
            new_unit_cost=old_unit_cost,
            new_vendor_id=vendor.id,
            new_invoice_no=layer.invoice_no,
        )
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
        flash("Batch details updated (purchase synced).", "success")
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
        old_purchased = Decimal(str(layer.quantity_received or 0))
        old_unit_cost = Decimal(str(layer.unit_cost or 0))
        reprice_batch(
            layer,
            unit_cost=unit_cost,
            sale_price=sale_price,
            user_id=current_user.id,
            notes=notes,
        )
        if unit_cost is not None:
            amend_purchase_for_layer(
                layer,
                old_purchased=old_purchased,
                new_purchased=old_purchased,
                old_unit_cost=old_unit_cost,
                new_unit_cost=Decimal(str(layer.unit_cost or 0)),
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
        flash("Batch pricing updated (purchase & vendor payable synced).", "success")
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

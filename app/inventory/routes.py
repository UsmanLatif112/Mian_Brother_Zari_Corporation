from decimal import Decimal
import json
from datetime import date, datetime

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from sqlalchemy import func
from app.extensions import db
from app.forms import ProductForm
from app.models import Category, Product, StockLayer, StockMovement, Vendor
from app.services.audit_service import log_audit
from app.services.fifo_service import (
    adjust_batch,
    reprice_all_remaining,
    reprice_batch,
)
from app.services.purchase_service import (
    amend_purchase_for_layer,
    record_purchase,
    reverse_purchase_for_layer,
)
from app.utils.party_filters import apply_party_active_filter, parse_party_active
from app.utils.working_date import get_working_date
from app.utils.decorators import permission_required
from app.utils.uploads import accept_uploaded_path, delete_image, save_image
from app.utils.weight_utils import normalize_weight_unit, parse_unit_weight

inventory_bp = Blueprint("inventory", __name__)


def _apply_product_weight(product, unit_weight_raw, weight_unit_raw):
    """Set product packaging weight and sync every batch to the same weight."""
    from app.services.fifo_service import _sync_all_layer_weights

    unit_weight = parse_unit_weight(unit_weight_raw)
    weight_unit = normalize_weight_unit(weight_unit_raw) if unit_weight else None
    if unit_weight and not weight_unit:
        weight_unit = "kg"
    product.unit_weight = unit_weight
    product.weight_unit = weight_unit if unit_weight else None
    if unit_weight:
        _sync_all_layer_weights(product)


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


def _apply_product_photo_path(product, photo_raw):
    """Set product photo from an ajax-uploaded path (JSON multi-add)."""
    path = accept_uploaded_path(photo_raw, "products")
    if path:
        if product.photo and product.photo != path:
            delete_image(product.photo)
        product.photo = path


def _parse_optional_date(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    return datetime.strptime(raw, "%Y-%m-%d").date()


def _parse_optional_text(raw):
    raw = (raw or "").strip()
    return raw or None


def _parse_purchase_quantities(item):
    """
    Parse purchase qty from line dict.
    Weighted products: sealed_bags + loose_weight_kg (preferred).
    Fallback: opening_stock / quantity decimal.
    Returns (total_qty, sealed_qty_or_none, open_weight_or_none).
    """
    uw_raw = item.get("unit_weight")
    loose_raw = item.get("loose_weight_kg")
    sealed_raw = item.get("sealed_bags")

    has_split = sealed_raw is not None and str(sealed_raw).strip() != ""
    if not has_split and loose_raw is not None and str(loose_raw).strip() != "":
        has_split = True

    if has_split:
        sealed = Decimal(str(sealed_raw or 0))
        loose = Decimal(str(loose_raw or 0))
        uw = parse_unit_weight(uw_raw) if uw_raw not in (None, "") else None
        if uw and uw > 0:
            total = sealed + (loose / uw)
        else:
            total = sealed
        if total <= 0:
            name = (item.get("name") or "product").strip()
            raise ValueError(f"Purchase quantity is required for {name}.")
        return total, sealed, loose

    try:
        qty = Decimal(str(item.get("opening_stock") or item.get("quantity") or 0))
    except Exception as exc:
        raise ValueError(f"Invalid purchase quantity for {item.get('name') or 'product'}.") from exc
    if qty <= 0:
        name = (item.get("name") or "product").strip()
        raise ValueError(f"Purchase quantity is required for {name}.")
    return qty, None, None


def _upsert_product_line(item):
    """
    Create or update one product from an inventory line dict.
    Returns (product, qty, purchase_line_dict, action).
    Does not commit.
    """
    from app.utils.product_codes import ensure_product_codes

    name = (item.get("name") or "").strip()
    if not name:
        raise ValueError("Product name is required on every row.")

    try:
        qty, sealed_qty, open_weight = _parse_purchase_quantities(item)
    except ValueError as exc:
        raise exc
    except Exception as exc:
        raise ValueError(f"Invalid purchase quantity for {name}.") from exc

    try:
        category_id = int(item.get("category_id") or 0)
    except (TypeError, ValueError):
        category_id = 0
    if not category_id:
        raise ValueError(f"Category is required for {name}.")

    try:
        purchase_price = Decimal(str(item.get("purchase_price") or 0))
        sale_price = Decimal(str(item.get("sale_price") or 0))
    except Exception as exc:
        raise ValueError(f"Invalid price values for {name}.") from exc

    sub_id = item.get("subcategory_id")
    try:
        sub_id = int(sub_id) if sub_id and int(sub_id) > 0 else None
    except (TypeError, ValueError):
        sub_id = None

    batch_number = (item.get("batch_number") or "").strip() or None
    if batch_number and batch_number.startswith("#"):
        batch_number = batch_number[1:].strip() or None

    expiry_raw = item.get("expiry_date")
    try:
        expiry_date = _parse_optional_date(expiry_raw) if expiry_raw else None
    except ValueError as exc:
        raise ValueError(f"Invalid expiry date for {name}. Use YYYY-MM-DD.") from exc

    existing_id = item.get("existing_product_id")
    brand = _parse_optional_text(item.get("brand"))
    description = _parse_optional_text(item.get("description"))
    try:
        minimum_stock = Decimal(str(item.get("minimum_stock") or 0))
    except Exception:
        minimum_stock = Decimal("0")

    if existing_id:
        try:
            product = db.session.get(Product, int(existing_id))
        except (TypeError, ValueError):
            product = None
        if not product or product.is_deleted:
            raise ValueError(f"Selected product not found: {name}.")
        product.name = name
        barcode_val = (item.get("barcode") or "").strip() or product.barcode
        product.barcode = barcode_val
        product.brand = brand
        product.category_id = category_id
        product.subcategory_id = sub_id
        product.purchase_price = purchase_price
        product.sale_price = sale_price
        product.minimum_stock = minimum_stock
        product.description = description
        _apply_product_weight(product, item.get("unit_weight"), item.get("weight_unit"))
        _apply_product_photo_path(product, item.get("photo"))
        action = "update"
    else:
        sku_val, barcode_val = ensure_product_codes(item.get("sku"), item.get("barcode"))
        if Product.query.filter_by(sku=sku_val).first():
            raise ValueError(f"SKU '{sku_val}' already exists. Clear and use a different SKU.")
        if barcode_val and Product.query.filter_by(barcode=barcode_val).first():
            raise ValueError(
                f"Barcode '{barcode_val}' already exists. Clear and use a different barcode."
            )
        product = Product(
            name=name,
            sku=sku_val,
            barcode=barcode_val,
            brand=brand,
            category_id=category_id,
            subcategory_id=sub_id,
            purchase_price=purchase_price,
            sale_price=sale_price,
            wholesale_price=Decimal(str(item.get("wholesale_price") or 0)),
            retail_price=Decimal(str(item.get("retail_price") or 0)),
            tax_rate=Decimal(str(item.get("tax_rate") or 0)),
            opening_stock=qty,
            current_stock=Decimal("0"),
            minimum_stock=minimum_stock,
            description=description,
            is_active=True,
        )
        _apply_product_weight(product, item.get("unit_weight"), item.get("weight_unit"))
        db.session.add(product)
        db.session.flush()
        _apply_product_photo_path(product, item.get("photo"))
        action = "create"

    purchase_line = {
        "product": product,
        "quantity": qty,
        "unit_price": purchase_price or product.purchase_price,
        "sale_price": sale_price or product.sale_price,
        "batch_number": batch_number,
        "expiry_date": expiry_date,
        "unit_weight": item.get("unit_weight"),
        "weight_unit": item.get("weight_unit"),
    }
    if sealed_qty is not None:
        purchase_line["sealed_qty"] = sealed_qty
        purchase_line["open_weight"] = open_weight or Decimal("0")
    return product, qty, purchase_line, action


def _create_inventory_products(data, *, as_json=True):
    """
    Create/restock one or more products from Add Product.

    Each product row becomes its own Purchase + vendor ledger entry so the
    ledger shows product / qty × weight @ rate on separate lines.
    Shared vendor invoice no. (if entered) is copied onto every row.
    """
    try:
        vendor_id = int(data.get("vendor_id") or 0)
    except (TypeError, ValueError):
        vendor_id = 0
    vendor = db.session.get(Vendor, vendor_id) if vendor_id else None
    if not vendor or vendor.is_deleted:
        msg = "Vendor is required."
        if as_json:
            return jsonify({"ok": False, "error": msg}), 400
        flash(msg, "danger")
        return _inventory_page(open_modal=True)

    items_raw = data.get("items")
    if not isinstance(items_raw, list) or not items_raw:
        msg = "Add at least one product row."
        if as_json:
            return jsonify({"ok": False, "error": msg}), 400
        flash(msg, "danger")
        return _inventory_page(open_modal=True)

    invoice_no = _parse_optional_text(data.get("invoice_no"))
    created = []
    restocked = []
    purchases = []

    try:
        for raw in items_raw:
            product, _qty, purchase_line, action = _upsert_product_line(raw or {})
            if action == "create":
                created.append(product)
            else:
                restocked.append(product)
            log_audit(action, "product", product.id, product.name)

            purchase = record_purchase(
                vendor_id=vendor.id,
                items=[purchase_line],
                user_id=current_user.id,
                invoice_no=invoice_no,
                purchase_date=get_working_date(),
                notes=f"Inventory add: {product.name}",
            )
            purchases.append(purchase)
            log_audit("create", "purchase", purchase.id, purchase.invoice_no)

        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        if as_json:
            return jsonify({"ok": False, "error": str(exc)}), 400
        flash(str(exc), "danger")
        return _inventory_page(open_modal=True)

    total = len(created) + len(restocked)
    first_purchase = purchases[0] if purchases else None
    invoice_labels = []
    for p in purchases[:4]:
        if p.invoice_no and p.invoice_no not in invoice_labels:
            invoice_labels.append(p.invoice_no)
    invoice_text = ", ".join(invoice_labels)
    if len(purchases) > 4:
        invoice_text += f" (+{len(purchases) - 4} more)"

    if total == 1 and first_purchase:
        product = (created or restocked)[0]
        if created:
            msg = (
                f"Product created and purchase {first_purchase.invoice_no} "
                f"recorded for {vendor.name}."
            )
        else:
            msg = (
                f"Stock added to {product.name}. Purchase {first_purchase.invoice_no} "
                f"recorded for {vendor.name}."
            )
        redirect_url = url_for("inventory.detail", product_id=product.id)
    else:
        msg = (
            f"{total} products saved as {total} separate purchases"
            f"{f' ({invoice_text})' if invoice_text else ''} for {vendor.name}."
        )
        redirect_url = url_for("inventory.index")

    if as_json:
        flash(msg, "success")
        products_payload = []
        for p in created + restocked:
            products_payload.append(
                {
                    "id": p.id,
                    "name": p.name,
                    "sku": p.sku,
                    "sale_price": float(p.sale_price or 0),
                    "list_price": float(p.sale_price or 0),
                    "unit_weight": float(p.unit_weight) if p.unit_weight is not None else None,
                    "weight_unit": p.weight_unit or "",
                    "photo_url": p.photo_url,
                    "stock_display": p.stock_display,
                }
            )
        first_product = (created or restocked)[0] if total else None
        return jsonify(
            {
                "ok": True,
                "message": msg,
                "purchase_id": first_purchase.id if first_purchase else None,
                "purchase_invoice": first_purchase.invoice_no if first_purchase else None,
                "purchase_ids": [p.id for p in purchases],
                "count": total,
                "redirect": redirect_url,
                "products": products_payload,
                "id": first_product.id if first_product else None,
                "name": first_product.name if first_product else None,
                "sale_price": float(first_product.sale_price or 0) if first_product else None,
                "list_price": float(first_product.sale_price or 0) if first_product else None,
                "unit_weight": (
                    float(first_product.unit_weight)
                    if first_product and first_product.unit_weight is not None
                    else None
                ),
                "weight_unit": first_product.weight_unit or "" if first_product else "",
                "photo_url": first_product.photo_url if first_product else None,
            }
        )

    flash(msg, "success")
    return redirect(redirect_url)


def _inventory_page(form=None, open_modal=False):
    from app.services.dashboard_service import _range_for_filter
    from app.services.fifo_service import (
        products_stock_as_of,
        stock_valuation,
        stock_valuation_as_of,
    )

    category_id = request.args.get("category_id", type=int)
    active_filter = parse_party_active(request.args.get("active"), default="all")
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
    q = apply_party_active_filter(q, Product, active_filter)
    if category_id:
        q = q.filter(
            db.or_(Product.category_id == category_id, Product.subcategory_id == category_id)
        )
    # Period filter is as-of end date for stock cards/rows — do not hide products
    # that only had receipts outside the window.

    products = q.order_by(Product.name).all()
    categories = Category.query.filter_by(is_deleted=False, parent_id=None).order_by(Category.name).all()
    if not open_modal:
        open_modal = bool(session.pop("open_inventory_modal", False))

    product_ids = [p.id for p in products]
    if range_end:
        stock_value = stock_valuation_as_of(range_end, product_ids=product_ids or None)
        stock_as_of = products_stock_as_of(range_end, product_ids=product_ids or None)
    elif category_id:
        stock_value = stock_valuation_as_of(None, product_ids=product_ids or None)
        stock_as_of = None
    else:
        stock_value = stock_valuation()
        stock_as_of = None

    from app.services.inventory_list_analytics_service import inventory_list_chart_metrics

    inventory_metrics = inventory_list_chart_metrics(
        period=period,
        start_date=period_start,
        end_date=period_end,
        product_ids=product_ids or None,
    )

    return render_template(
        "inventory/index.html",
        products=products,
        form=form or ProductForm(),
        open_modal=open_modal,
        categories=categories,
        selected_category=category_id or "",
        selected_active=active_filter,
        selected_period=period,
        start_date=start_raw,
        end_date=end_raw,
        stock_value=stock_value,
        total_purchases=inventory_metrics["total_purchases"],
        total_sales=inventory_metrics["total_sales"],
        purchase_count=inventory_metrics["purchase_count"],
        sale_count=inventory_metrics["sale_count"],
        product_count=len(products),
        stock_as_of=stock_as_of,
        period_as_of=range_end,
        inventory_chart=inventory_metrics["chart"],
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

    data = request.get_json(silent=True)
    if data is not None:
        return _create_inventory_products(data, as_json=True)

    # Legacy single-product multipart form (kept for compatibility)
    form = ProductForm()
    if form.validate_on_submit():
        payload = {
            "vendor_id": form.vendor_id.data,
            "invoice_no": form.invoice_no.data,
            "items": [
                {
                    "name": form.name.data,
                    "existing_product_id": form.existing_product_id.data,
                    "sku": form.sku.data,
                    "barcode": form.barcode.data,
                    "brand": form.brand.data,
                    "category_id": form.category_id.data,
                    "subcategory_id": form.subcategory_id.data,
                    "purchase_price": form.purchase_price.data,
                    "sale_price": form.sale_price.data,
                    "wholesale_price": form.wholesale_price.data,
                    "retail_price": form.retail_price.data,
                    "tax_rate": form.tax_rate.data,
                    "opening_stock": form.opening_stock.data,
                    "minimum_stock": form.minimum_stock.data,
                    "unit_weight": form.unit_weight.data,
                    "weight_unit": form.weight_unit.data,
                    "batch_number": form.batch_number.data,
                    "expiry_date": (
                        form.expiry_date.data.isoformat() if form.expiry_date.data else ""
                    ),
                    "description": form.description.data,
                    "photo": request.form.get("photo_path") or "",
                }
            ],
        }
        # Apply multipart file upload onto the product after create path runs via photo_path;
        # if only a file was uploaded, stash it after upsert by using form flow photo apply.
        result = _create_inventory_products(payload, as_json=False)
        return result
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
        _apply_product_weight(
            product,
            request.form.get("unit_weight"),
            request.form.get("weight_unit"),
        )
        _apply_product_photo(product)
        product.is_active = request.form.get("is_active", "1") == "1"
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

    layers = layers_q.order_by(StockLayer.id.desc()).all()
    movements = (
        movements_q.order_by(StockMovement.created_at.desc(), StockMovement.id.desc()).limit(100).all()
    )
    open_batch_count = sum(1 for L in layers if L.has_stock())
    closed_batch_count = sum(1 for L in layers if not L.has_stock())

    from app.services.fifo_service import product_stock_as_of, stock_valuation_as_of
    from app.services.inventory_loss_service import list_open_backorders, open_backorder_qty
    from app.services.product_analytics_service import product_performance_metrics
    from app.utils.weight_utils import format_qty_display

    pending_backorder_qty = open_backorder_qty(product.id)
    pending_backorders = list_open_backorders(product.id)
    pending_backorder_display = format_qty_display(
        pending_backorder_qty,
        product.unit_weight,
        product.weight_unit or "kg",
    )

    if range_end:
        total_stock = product_stock_as_of(product.id, range_end)
        total_stock_value = stock_valuation_as_of(range_end, product_ids=[product.id])
        stock_display = f"{float(total_stock):.3f}".rstrip("0").rstrip(".")
    else:
        total_stock = Decimal(str(product.current_stock or 0))
        total_stock_value = sum(
            (L.stock_qty_equivalent * (L.unit_cost or 0) for L in layers if L.has_stock()),
            Decimal("0"),
        )
        stock_display = product.stock_total_display

    performance = product_performance_metrics(
        product.id,
        period=period,
        start=range_start,
        end=range_end,
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
        stock_display=stock_display,
        open_batch_count=open_batch_count,
        closed_batch_count=closed_batch_count,
        pending_backorder_qty=pending_backorder_qty,
        pending_backorder_display=pending_backorder_display,
        pending_backorders=pending_backorders,
        form=form,
        today=date.today(),  # calendar day for expiry highlighting only
        selected_period=period,
        start_date=start_raw,
        end_date=end_raw,
        period_as_of=range_end,
        performance=performance,
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
        # One product = one weight: update product and sync all batches.
        _apply_product_weight(
            product,
            request.form.get("unit_weight"),
            request.form.get("weight_unit"),
        )
        layer.unit_weight = product.unit_weight
        layer.weight_unit = product.weight_unit

        try:
            open_w = Decimal(str(request.form.get("open_weight_remaining") or 0))
        except Exception:
            open_w = Decimal("0")
        if open_w < 0:
            raise ValueError("Open weight cannot be negative.")
        layer.open_weight_remaining = open_w

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
            raise ValueError("Sealed bags cannot be more than purchased qty.")

        layer.quantity_received = new_purchased
        current_qty = Decimal(str(layer.quantity_remaining or 0))
        if new_qty != current_qty:
            adj_notes = (request.form.get("adjustment_notes") or "").strip() or None
            adjust_batch(
                layer,
                new_qty,
                current_user.id,
                notes=adj_notes,
            )
        layer.quantity_received = new_purchased

        reprice_batch(
            layer,
            unit_cost=purchase_price,
            sale_price=sale_price,
            user_id=current_user.id,
            notes=None,  # movement notes = "Reprice: cost old→new"
        )

        from app.services.fifo_service import sync_product_stock

        sync_product_stock(product)

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
                    "open_weight_remaining": float(layer.open_weight_remaining or 0),
                    "quantity_received": float(layer.quantity_received or 0),
                    "unit_cost": float(layer.unit_cost or 0),
                    "sale_price": float(layer.sale_price or 0) if layer.sale_price is not None else None,
                    "unit_weight": float(layer.unit_weight) if layer.unit_weight is not None else None,
                    "batch_number": layer.batch_number,
                    "expiry_date": layer.expiry_date.isoformat() if layer.expiry_date else None,
                }
            ),
        )
        db.session.commit()
        flash("Inventory entry updated (purchase, vendor payable & sold cost synced).", "success")
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
        flash("Batch pricing updated (purchase, sold cost & dashboard synced).", "success")
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
        flash(
            f"Repriced {count} batch(es); sold cost of goods & dashboard updated.",
            "success",
        )
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
        # Reverse linked purchase / vendor payable before clearing stock.
        reversed_info = reverse_purchase_for_layer(
            layer, notes=f"Batch #{layer_id} deleted"
        )
        remaining = Decimal(str(layer.quantity_remaining or 0))
        if remaining > 0:
            adjust_batch(layer, Decimal("0"), current_user.id, notes="Batch cleared / deleted")
        db.session.delete(layer)
        db.session.flush()
        product = db.session.get(Product, product_id)
        if product:
            open_qty = (
                db.session.query(func.coalesce(func.sum(StockLayer.quantity_remaining), 0))
                .filter(StockLayer.product_id == product_id)
                .scalar()
            )
            product.current_stock = Decimal(str(open_qty or 0))
        audit_detail = f"product={product_id}"
        if reversed_info:
            audit_detail += (
                f" purchase={reversed_info.get('purchase_id')}"
                f" vendor={reversed_info.get('vendor_id')}"
                f" purchase_deleted={reversed_info.get('deleted')}"
            )
            log_audit(
                "delete" if reversed_info.get("deleted") else "update",
                "purchase",
                reversed_info.get("purchase_id"),
                reversed_info.get("invoice_no"),
            )
        log_audit("delete", "stock_layer", layer_id, audit_detail)
        db.session.commit()
        if reversed_info and reversed_info.get("deleted"):
            flash("Batch deleted. Linked purchase removed and vendor balance updated.", "success")
        elif reversed_info:
            flash("Batch deleted. Purchase and vendor balance recalculated.", "success")
        else:
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
        layers = StockLayer.query.filter_by(product_id=product.id).all()
        purchases_removed = 0
        for layer in layers:
            info = reverse_purchase_for_layer(
                layer, notes=f"Product deleted: {product.name}"
            )
            if info and info.get("deleted"):
                purchases_removed += 1
            remaining = Decimal(str(layer.quantity_remaining or 0))
            if remaining > 0:
                adjust_batch(
                    layer,
                    Decimal("0"),
                    current_user.id,
                    notes="Product deleted — stock cleared",
                )
            db.session.delete(layer)

        product.is_deleted = True
        product.current_stock = Decimal("0")
        log_audit("delete", "product", product.id, product.name)
        db.session.commit()
        if purchases_removed:
            flash(
                f"Product deleted. {purchases_removed} linked purchase(s) removed; "
                "vendor balances and stock value recalculated.",
                "success",
            )
        else:
            flash(
                "Product deleted (soft). Linked purchases reversed where needed; stock cleared.",
                "success",
            )
        return redirect(url_for("inventory.index"))
    except Exception as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("inventory.detail", product_id=product_id))

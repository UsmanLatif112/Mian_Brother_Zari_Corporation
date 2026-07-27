from flask import Blueprint, jsonify, request
from flask_login import login_required
from sqlalchemy import or_
from decimal import Decimal

from app.extensions import db
from app.forms import CustomerForm
from app.models import Category, Customer, ExpenseCategory, Product, Sale, Vendor

api_bp = Blueprint("api", __name__)


@api_bp.route("/internet")
@login_required
def internet_status():
    from app.services.network_service import is_cloud_registry_reachable, is_internet_available

    purpose = (request.args.get("for") or "").strip().lower()
    if purpose in ("registration", "registry", "mysql"):
        online = is_cloud_registry_reachable()
    else:
        online = is_internet_available()
    return jsonify({"online": online})


@api_bp.route("/working-date", methods=["GET"])
@login_required
def working_date_status():
    from app.utils.working_date import working_date_payload

    return jsonify({"ok": True, **working_date_payload()})


@api_bp.route("/working-date", methods=["POST"])
@login_required
def working_date_set():
    from flask_login import current_user

    from app.utils.working_date import can_manage_working_date, set_working_date, working_date_payload

    if not can_manage_working_date(current_user):
        return jsonify({"ok": False, "error": "Only Admin can change the working date."}), 403

    data = request.get_json(silent=True) or {}
    if data.get("clear"):
        set_working_date(None)
        return jsonify({"ok": True, "message": "Working date reset to today.", **working_date_payload()})

    raw = data.get("date") or data.get("working_date")
    if not raw:
        return jsonify({"ok": False, "error": "Date is required."}), 400
    try:
        set_working_date(raw)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "message": "Working date updated.", **working_date_payload()})


@api_bp.route("/updates/check")
@login_required
def updates_check():
    from app.services.update_service import check_for_update

    respect_skip = request.args.get("respect_skip", "1") != "0"
    return jsonify(check_for_update(respect_skip=respect_skip))


@api_bp.route("/updates/skip", methods=["POST"])
@login_required
def updates_skip():
    from app.services.update_service import set_skipped_build

    data = request.get_json(silent=True) or {}
    try:
        build = int(data.get("build") or 0)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Invalid build number."}), 400
    if build <= 0:
        return jsonify({"ok": False, "error": "Build number is required."}), 400
    set_skipped_build(build)
    return jsonify({"ok": True, "message": "This version will not be prompted again."})


@api_bp.route("/updates/apply", methods=["POST"])
@login_required
def updates_apply():
    from app.services.update_service import prepare_update_apply

    result = prepare_update_apply()
    code = 200 if result.get("ok") else 400
    if result.get("offline"):
        code = 503
    return jsonify(result), code


@api_bp.route("/toasts")
@login_required
def poll_toasts():
    from app.services.toast_service import fetch_toasts

    try:
        after_id = int(request.args.get("after", 0) or 0)
    except (TypeError, ValueError):
        after_id = 0
    return jsonify({"toasts": fetch_toasts(after_id=after_id)})


@api_bp.route("/search")
@login_required
def global_search():
    from app.services.page_search_service import search_scoped

    q = (request.args.get("q") or "").strip()
    # Scope must come from the page (client). Never infer from /api/search.
    scope = (request.args.get("scope") or "").strip().lower()
    if len(q) < 2 or not scope or scope == "none":
        return jsonify({"results": []})
    return jsonify({"results": search_scoped(scope, q)})


@api_bp.route("/categories/lookup")
@login_required
def categories_lookup():
    q = (request.args.get("q") or "").strip()
    parent_raw = request.args.get("parent_id")
    query = Category.query.filter(Category.is_deleted.is_(False))
    if parent_raw in (None, "", "0"):
        # Top-level categories only
        query = query.filter(Category.parent_id.is_(None))
    else:
        try:
            parent_id = int(parent_raw)
        except (TypeError, ValueError):
            return jsonify({"results": []})
        query = query.filter(Category.parent_id == parent_id)
    if q:
        query = query.filter(Category.name.ilike(f"%{q}%"))
    rows = query.order_by(Category.name).limit(20).all()
    return jsonify(
        {
            "results": [
                {"id": c.id, "name": c.name, "parent_id": c.parent_id, "label": c.name}
                for c in rows
            ]
        }
    )


@api_bp.route("/categories/quick", methods=["POST"])
@login_required
def categories_quick():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Category name is required."}), 400

    parent_id = data.get("parent_id")
    try:
        parent_id = int(parent_id) if parent_id not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        parent_id = None

    if parent_id:
        parent = db.session.get(Category, parent_id)
        if not parent or parent.is_deleted:
            return jsonify({"ok": False, "error": "Parent category not found."}), 400

    existing_q = Category.query.filter(
        Category.name.ilike(name),
        Category.is_deleted.is_(False),
    )
    if parent_id:
        existing_q = existing_q.filter(Category.parent_id == parent_id)
    else:
        existing_q = existing_q.filter(Category.parent_id.is_(None))
    existing = existing_q.first()
    if existing:
        return jsonify({"ok": True, "id": existing.id, "name": existing.name, "parent_id": existing.parent_id})

    cat = Category(name=name, parent_id=parent_id)
    db.session.add(cat)
    db.session.commit()
    return jsonify({"ok": True, "id": cat.id, "name": cat.name, "parent_id": cat.parent_id})


@api_bp.route("/customers/lookup")
@login_required
def customers_lookup():
    q = (request.args.get("q") or "").strip()
    query = Customer.query.filter(Customer.is_deleted.is_(False))
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Customer.name.ilike(like), Customer.phone.ilike(like)))
    rows = query.order_by(Customer.name).limit(20).all()
    return jsonify(
        {
            "results": [
                {
                    "id": c.id,
                    "name": c.name,
                    "phone": c.phone or "",
                    "type": getattr(c, "customer_type", "good"),
                    "label": f"{c.name}" + (f" ({c.phone})" if c.phone else ""),
                }
                for c in rows
            ]
        }
    )


@api_bp.route("/vendors/lookup")
@login_required
def vendors_lookup():
    q = (request.args.get("q") or "").strip()
    query = Vendor.query.filter(Vendor.is_deleted.is_(False))
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Vendor.name.ilike(like), Vendor.phone.ilike(like)))
    rows = query.order_by(Vendor.name).limit(20).all()
    return jsonify(
        {
            "results": [
                {
                    "id": v.id,
                    "name": v.name,
                    "phone": v.phone or "",
                    "label": f"{v.name}" + (f" ({v.phone})" if v.phone else ""),
                }
                for v in rows
            ]
        }
    )


@api_bp.route("/expense-categories/quick", methods=["POST"])
@login_required
def quick_expense_category():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Category name is required."}), 400

    existing = ExpenseCategory.query.filter(ExpenseCategory.name.ilike(name)).first()
    if existing:
        if existing.is_deleted:
            existing.is_deleted = False
            existing.deleted_at = None
            db.session.commit()
        return jsonify({"ok": True, "id": existing.id, "name": existing.name})

    cat = ExpenseCategory(name=name)
    db.session.add(cat)
    db.session.commit()
    return jsonify({"ok": True, "id": cat.id, "name": cat.name})


@api_bp.route("/vendors/quick", methods=["POST"])
@login_required
def quick_vendor():
    from app.utils.uploads import accept_uploaded_path

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Vendor name is required."}), 400

    opening = Decimal(str(data.get("opening_balance") or 0))
    photo = accept_uploaded_path(data.get("photo"), "vendors")
    vendor = Vendor(
        name=name,
        phone=(data.get("phone") or "").strip() or None,
        address=(data.get("address") or "").strip() or None,
        opening_balance=opening,
        balance=opening,
        notes=(data.get("notes") or "").strip() or None,
        photo=photo,
    )
    db.session.add(vendor)
    db.session.commit()
    return jsonify({"ok": True, "id": vendor.id, "name": vendor.name, "phone": vendor.phone or ""})


@api_bp.route("/uploads/photo", methods=["POST"])
@login_required
def upload_photo():
    from app.utils.uploads import image_url, save_image

    folder = (request.form.get("folder") or "misc").strip().lower()
    allowed = {"customers", "vendors", "sales", "products", "misc"}
    if folder not in allowed:
        return jsonify({"ok": False, "error": "Invalid upload folder."}), 400
    try:
        path = save_image(request.files.get("photo"), folder)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    if not path:
        return jsonify({"ok": False, "error": "No image selected."}), 400
    return jsonify({"ok": True, "path": path, "url": image_url(path)})


@api_bp.route("/products/next-codes")
@login_required
def products_next_codes():
    from app.models import StockLayer
    from app.utils.product_codes import generate_unique_barcode, generate_unique_sku

    try:
        next_batch = (db.session.query(db.func.max(StockLayer.id)).scalar() or 0) + 1
        return jsonify(
            {
                "ok": True,
                "sku": generate_unique_sku(),
                "barcode": generate_unique_barcode(),
                "next_batch_id": int(next_batch),
            }
        )
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@api_bp.route("/products/lookup")
@login_required
def products_lookup():
    from app.services.fifo_service import next_fifo_sale_price

    q = (request.args.get("q") or "").strip()
    query = Product.query.filter(Product.is_deleted.is_(False))
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(Product.name.ilike(like), Product.sku.ilike(like), Product.barcode.ilike(like))
        )
    rows = query.order_by(Product.name).limit(20).all()
    results = []
    for p in rows:
        fifo_price = float(next_fifo_sale_price(p))
        results.append(
            {
                "id": p.id,
                "name": p.name,
                "sku": p.sku,
                "barcode": p.barcode or "",
                "brand": p.brand or "",
                "category_id": p.category_id,
                "category_name": p.category.name if p.category else "",
                "subcategory_id": p.subcategory_id or "",
                "subcategory_name": p.subcategory.name if p.subcategory else "",
                "sale_price": fifo_price,
                "list_price": float(p.sale_price or 0),
                "purchase_price": float(p.purchase_price or 0),
                "minimum_stock": float(p.minimum_stock or 0),
                "description": p.description or "",
                "stock": float(p.current_stock or 0),
                "photo_url": p.photo_url,
                "photo": p.photo or "",
                "label": f"{p.name} ({p.sku}) — {fifo_price:.2f} · stock {float(p.current_stock or 0):.3g}",
            }
        )
    return jsonify({"results": results})


@api_bp.route("/customers/quick", methods=["POST"])
@login_required
def quick_customer():
    from datetime import date

    from app.services.ledger_service import post_ledger_entry
    from app.utils.uploads import accept_uploaded_path

    from app.utils.working_date import get_working_date

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Customer name is required."}), 400

    joined = data.get("joined_date") or get_working_date().isoformat()
    try:
        joined_date = date.fromisoformat(joined)
    except ValueError:
        joined_date = get_working_date()

    opening = Decimal(str(data.get("opening_balance") or data.get("old_account_balance") or 0))
    photo = accept_uploaded_path(data.get("photo"), "customers")
    customer = Customer(
        name=name,
        phone=(data.get("phone") or "").strip() or None,
        address=(data.get("address") or "").strip() or None,
        old_book_no=(data.get("old_book_no") or "").strip() or None,
        joined_date=joined_date,
        customer_type=data.get("customer_type") or "good",
        opening_balance=opening,
        balance=opening,
        credit_limit=data.get("credit_limit") or 0,
        notes=data.get("notes"),
        photo=photo,
    )
    db.session.add(customer)
    db.session.flush()
    if opening != 0:
        # Positive opening = customer owes us (old credit)
        debit = opening if opening > 0 else Decimal("0")
        credit = abs(opening) if opening < 0 else Decimal("0")
        post_ledger_entry(
            "customer",
            customer.id,
            "opening",
            debit=debit,
            credit=credit,
            entry_date=joined_date,
            notes=f"Old book balance" + (f" ({customer.old_book_no})" if customer.old_book_no else ""),
        )
    db.session.commit()
    return jsonify({"ok": True, "id": customer.id, "name": customer.name, "phone": customer.phone or ""})


@api_bp.route("/products/quick", methods=["POST"])
@login_required
def quick_product():
    from app.services.fifo_service import add_stock_layer

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Product name is required."}), 400

    category_id = data.get("category_id")
    if category_id:
        cat = db.session.get(Category, int(category_id))
    else:
        cat = Category.query.filter_by(is_deleted=False, parent_id=None).first()
    if not cat:
        return jsonify({"ok": False, "error": "Create a category first in Inventory."}), 400

    opening = float(data.get("opening_stock") or 0)
    purchase = float(data.get("purchase_price") or 0)
    sale_price = float(data.get("sale_price") or 0)
    sub_id = data.get("subcategory_id")
    try:
        sub_id = int(sub_id) if sub_id and int(sub_id) > 0 else None
    except (TypeError, ValueError):
        sub_id = None

    batch_number = (data.get("batch_number") or "").strip() or None
    expiry_raw = (data.get("expiry_date") or "").strip()
    expiry_date = None
    if expiry_raw:
        from datetime import datetime

        try:
            expiry_date = datetime.strptime(expiry_raw, "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"ok": False, "error": "Invalid expiry date. Use YYYY-MM-DD."}), 400

    from app.utils.product_codes import ensure_product_codes
    from app.utils.uploads import accept_uploaded_path

    try:
        sku, barcode = ensure_product_codes(data.get("sku"), data.get("barcode"))
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    if Product.query.filter_by(sku=sku).first():
        return jsonify({"ok": False, "error": f"SKU '{sku}' already exists."}), 400
    if barcode and Product.query.filter_by(barcode=barcode).first():
        return jsonify({"ok": False, "error": f"Barcode '{barcode}' already exists."}), 400
    photo = accept_uploaded_path(data.get("photo"), "products")
    product = Product(
        name=name,
        sku=sku,
        barcode=barcode,
        brand=(data.get("brand") or "").strip() or None,
        category_id=cat.id,
        subcategory_id=sub_id,
        sale_price=sale_price,
        purchase_price=purchase,
        current_stock=opening,
        opening_stock=opening,
        minimum_stock=data.get("minimum_stock") or 0,
        description=(data.get("description") or "").strip() or None,
        photo=photo,
    )
    db.session.add(product)
    db.session.flush()
    if opening > 0:
        add_stock_layer(
            product.id,
            opening,
            purchase or sale_price,
            "opening",
            sale_price=sale_price,
            batch_number=batch_number,
            expiry_date=expiry_date,
        )
    db.session.commit()
    return jsonify(
        {
            "ok": True,
            "id": product.id,
            "name": product.name,
            "sku": product.sku,
            "sale_price": float(product.sale_price or 0),
            "stock": float(product.current_stock or 0),
            "photo_url": product.photo_url,
        }
    )

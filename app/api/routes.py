from flask import Blueprint, jsonify, request
from flask_login import login_required
from sqlalchemy import or_
from decimal import Decimal

from app.utils.party_filters import apply_party_active_filter, parse_party_active
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
    from app.services.category_service import get_or_create_category

    data = request.get_json(silent=True) or {}
    name = data.get("name")
    parent_id = data.get("parent_id")
    try:
        cat, _created = get_or_create_category(name, parent_id=parent_id)
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 400

    return jsonify(
        {"ok": True, "id": cat.id, "name": cat.name, "parent_id": cat.parent_id}
    )


@api_bp.route("/customers/lookup")
@login_required
def customers_lookup():
    q = (request.args.get("q") or "").strip()
    query = Customer.query.filter(
        Customer.is_deleted.is_(False),
        Customer.is_active.is_(True),
    )
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
    query = Vendor.query.filter(
        Vendor.is_deleted.is_(False),
        Vendor.is_active.is_(True),
    )
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


@api_bp.route("/salesmen/lookup")
@login_required
def salesmen_lookup():
    from app.models import Salesman

    q = (request.args.get("q") or "").strip()
    query = Salesman.query.filter(Salesman.is_deleted.is_(False))
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                Salesman.name.ilike(like),
                Salesman.phone.ilike(like),
                Salesman.company.ilike(like),
            )
        )
    rows = query.order_by(Salesman.name).limit(20).all()
    return jsonify(
        {
            "results": [
                {
                    "id": s.id,
                    "name": s.name,
                    "phone": s.phone or "",
                    "company": s.company or "",
                    "label": f"{s.name}"
                    + (f" · {s.company}" if s.company else "")
                    + (f" ({s.phone})" if s.phone else ""),
                }
                for s in rows
            ]
        }
    )


@api_bp.route("/salesmen/quick", methods=["POST"])
@login_required
def quick_salesman():
    from app.models import Salesman
    from app.services.ledger_service import post_ledger_entry
    from app.utils.uploads import accept_uploaded_path
    from app.utils.working_date import get_working_date

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Salesman name is required."}), 400

    opening = Decimal(str(data.get("opening_balance") or 0))
    photo = accept_uploaded_path(data.get("photo"), "salesmen")
    salesman = Salesman(
        name=name,
        phone=(data.get("phone") or "").strip() or None,
        company=(data.get("company") or "").strip() or None,
        address=(data.get("address") or "").strip() or None,
        opening_balance=opening,
        balance=opening,
        notes=(data.get("notes") or "").strip() or None,
        photo=photo,
    )
    db.session.add(salesman)
    db.session.flush()
    if opening:
        opening_d = Decimal(str(opening))
        debit = opening_d if opening_d > 0 else Decimal("0")
        credit = abs(opening_d) if opening_d < 0 else Decimal("0")
        post_ledger_entry(
            "salesman",
            salesman.id,
            "opening",
            debit=debit,
            credit=credit,
            entry_date=get_working_date(),
            notes="Opening balance",
        )
    db.session.commit()
    return jsonify({"ok": True, "id": salesman.id, "name": salesman.name})


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
    photo_paths = data.get("photo_paths") or []
    if isinstance(photo_paths, str):
        photo_paths = [photo_paths]
    vendor = Vendor(
        name=name,
        phone=(data.get("phone") or "").strip() or None,
        cnic=(data.get("cnic") or "").strip() or None,
        address=(data.get("address") or "").strip() or None,
        opening_balance=opening,
        balance=opening,
        notes=(data.get("notes") or "").strip() or None,
        photo=photo,
    )
    db.session.add(vendor)
    db.session.flush()
    from app.models import VendorPhoto

    paths = []
    if photo:
        paths.append(photo)
    for raw in photo_paths:
        clean = accept_uploaded_path(raw, "vendors")
        if clean and clean not in paths:
            paths.append(clean)
    for idx, path in enumerate(paths):
        db.session.add(
            VendorPhoto(vendor_id=vendor.id, path=path, sort_order=idx)
        )
    if paths:
        vendor.photo = paths[0]
    if opening:
        from app.services.ledger_service import post_ledger_entry
        from app.utils.working_date import get_working_date

        opening_d = Decimal(str(opening))
        debit = opening_d if opening_d > 0 else Decimal("0")
        credit = abs(opening_d) if opening_d < 0 else Decimal("0")
        post_ledger_entry(
            "vendor",
            vendor.id,
            "opening",
            debit=debit,
            credit=credit,
            entry_date=get_working_date(),
            notes="Opening balance",
        )
    db.session.commit()
    return jsonify({"ok": True, "id": vendor.id, "name": vendor.name, "phone": vendor.phone or ""})


@api_bp.route("/uploads/photo", methods=["POST"])
@login_required
def upload_photo():
    from app.utils.uploads import image_url, save_image

    folder = (request.form.get("folder") or "misc").strip().lower()
    allowed = {"customers", "vendors", "salesmen", "sales", "products", "misc", "logos"}
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
    from app.services.fifo_service import next_product_batch_seq
    from app.utils.product_codes import generate_unique_barcode, generate_unique_sku

    try:
        product_id = request.args.get("product_id", type=int)
        next_batch = next_product_batch_seq(product_id) if product_id else 1
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
    from app.services.fifo_service import (
        next_fifo_packaging,
        next_fifo_sale_price,
        next_product_batch_seq,
    )

    q = (request.args.get("q") or "").strip()
    active_filter = parse_party_active(request.args.get("active"), default="all")
    query = Product.query.filter(Product.is_deleted.is_(False))
    query = apply_party_active_filter(query, Product, active_filter)
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(Product.name.ilike(like), Product.sku.ilike(like), Product.barcode.ilike(like))
        )
    rows = query.order_by(Product.name).limit(20).all()
    results = []
    for p in rows:
        from app.utils.weight_utils import format_stock_display

        stock_label = format_stock_display(p)
        fifo_uw, fifo_wu = next_fifo_packaging(p)
        unit_weight = float(fifo_uw) if fifo_uw is not None else (
            float(p.unit_weight) if p.unit_weight is not None else None
        )
        weight_unit = fifo_wu or p.weight_unit or ""
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
                "list_price": fifo_price,
                "purchase_price": float(p.purchase_price or 0),
                "minimum_stock": float(p.minimum_stock or 0),
                "description": p.description or "",
                "stock": float(p.current_stock or 0),
                "stock_display": stock_label,
                "unit_weight": unit_weight,
                "weight_unit": weight_unit,
                "photo_url": p.photo_url,
                "photo": p.photo or "",
                "next_batch": next_product_batch_seq(p.id),
                "label": f"{p.name} ({p.sku}) — {fifo_price:.2f} · stock {stock_label}",
            }
        )
    return jsonify({"results": results})


@api_bp.route("/customers/quick", methods=["POST"])
@login_required
def quick_customer():
    from datetime import date

    from app.services.ledger_service import sync_party_opening_entry
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
    photo_paths = data.get("photo_paths") or []
    if isinstance(photo_paths, str):
        photo_paths = [photo_paths]
    customer = Customer(
        name=name,
        phone=(data.get("phone") or "").strip() or None,
        cnic=(data.get("cnic") or "").strip() or None,
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
    from app.models import CustomerPhoto

    paths = []
    if photo:
        paths.append(photo)
    for raw in photo_paths:
        clean = accept_uploaded_path(raw, "customers")
        if clean and clean not in paths:
            paths.append(clean)
    for idx, path in enumerate(paths):
        db.session.add(
            CustomerPhoto(customer_id=customer.id, path=path, sort_order=idx)
        )
    if paths:
        customer.photo = paths[0]
    sync_party_opening_entry(
        "customer",
        customer.id,
        opening,
        entry_date=joined_date,
        notes="Old book balance"
        + (f" ({customer.old_book_no})" if customer.old_book_no else ""),
    )
    db.session.commit()
    return jsonify({"ok": True, "id": customer.id, "name": customer.name, "phone": customer.phone or ""})


@api_bp.route("/products/batch", methods=["POST"])
@login_required
def products_batch():
    """Same as Inventory Add Products — for sales page quick-add (login only)."""
    from app.inventory.routes import _create_inventory_products

    data = request.get_json(silent=True) or {}
    return _create_inventory_products(data, as_json=True)


@api_bp.route("/products/quick", methods=["POST"])
@login_required
def quick_product():
    """Same logic as Inventory Add Product: require vendor + qty, record a purchase."""
    from decimal import Decimal

    from flask_login import current_user

    from app.models import Vendor
    from app.services.audit_service import log_audit
    from app.services.purchase_service import record_purchase
    from app.utils.product_codes import ensure_product_codes
    from app.utils.uploads import accept_uploaded_path
    from app.utils.working_date import get_working_date

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Product name is required."}), 400

    try:
        vendor_id = int(data.get("vendor_id") or 0)
    except (TypeError, ValueError):
        vendor_id = 0
    vendor = db.session.get(Vendor, vendor_id) if vendor_id else None
    if not vendor or vendor.is_deleted:
        return jsonify({"ok": False, "error": "Vendor is required."}), 400

    try:
        qty = Decimal(str(data.get("opening_stock") or 0))
    except Exception:
        qty = Decimal("0")
    if qty <= 0:
        return jsonify({"ok": False, "error": "Purchase quantity is required."}), 400

    category_id = data.get("category_id")
    if category_id:
        cat = db.session.get(Category, int(category_id))
    else:
        cat = Category.query.filter_by(is_deleted=False, parent_id=None).first()
    if not cat:
        return jsonify({"ok": False, "error": "Create a category first in Inventory."}), 400

    try:
        purchase_price = Decimal(str(data.get("purchase_price") or 0))
        sale_price = Decimal(str(data.get("sale_price") or 0))
    except Exception:
        return jsonify({"ok": False, "error": "Invalid price values."}), 400

    sub_id = data.get("subcategory_id")
    try:
        sub_id = int(sub_id) if sub_id and int(sub_id) > 0 else None
    except (TypeError, ValueError):
        sub_id = None

    batch_number = (data.get("batch_number") or "").strip() or None
    if batch_number and batch_number.startswith("#"):
        batch_number = batch_number[1:].strip() or None
    invoice_no = (data.get("invoice_no") or "").strip() or None
    expiry_raw = (data.get("expiry_date") or "").strip()
    expiry_date = None
    if expiry_raw:
        from datetime import datetime

        try:
            expiry_date = datetime.strptime(expiry_raw, "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"ok": False, "error": "Invalid expiry date. Use YYYY-MM-DD."}), 400

    photo = accept_uploaded_path(data.get("photo"), "products")
    existing_id = data.get("existing_product_id")
    action = "create"
    product = None
    try:
        from app.utils.weight_utils import normalize_weight_unit, parse_unit_weight

        uw = parse_unit_weight(data.get("unit_weight"))
        wu = normalize_weight_unit(data.get("weight_unit")) if uw else None
        if uw and not wu:
            wu = "kg"
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    if existing_id:
        try:
            product = db.session.get(Product, int(existing_id))
        except (TypeError, ValueError):
            product = None
        if not product or product.is_deleted:
            return jsonify({"ok": False, "error": "Selected product not found."}), 400
        product.name = name
        barcode_val = (data.get("barcode") or "").strip() or product.barcode
        product.barcode = barcode_val
        product.brand = (data.get("brand") or "").strip() or None
        product.category_id = cat.id
        product.subcategory_id = sub_id
        product.purchase_price = purchase_price
        product.sale_price = sale_price
        product.minimum_stock = data.get("minimum_stock") or product.minimum_stock or 0
        product.description = (data.get("description") or "").strip() or None
        product.unit_weight = uw
        product.weight_unit = wu if uw else None
        if photo:
            product.photo = photo
        action = "update"
    else:
        try:
            sku, barcode = ensure_product_codes(data.get("sku"), data.get("barcode"))
        except RuntimeError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        if Product.query.filter_by(sku=sku).first():
            return jsonify({"ok": False, "error": f"SKU '{sku}' already exists."}), 400
        if barcode and Product.query.filter_by(barcode=barcode).first():
            return jsonify({"ok": False, "error": f"Barcode '{barcode}' already exists."}), 400

        product = Product(
            name=name,
            sku=sku,
            barcode=barcode,
            brand=(data.get("brand") or "").strip() or None,
            category_id=cat.id,
            subcategory_id=sub_id,
            sale_price=sale_price,
            purchase_price=purchase_price,
            current_stock=Decimal("0"),
            opening_stock=qty,
            minimum_stock=data.get("minimum_stock") or 0,
            description=(data.get("description") or "").strip() or None,
            photo=photo,
            unit_weight=uw,
            weight_unit=wu if uw else None,
            is_active=True,
        )
        db.session.add(product)
        db.session.flush()

    try:
        purchase = record_purchase(
            vendor_id=vendor.id,
            items=[
                {
                    "product": product,
                    "quantity": qty,
                    "unit_price": purchase_price or product.purchase_price,
                    "sale_price": sale_price or product.sale_price,
                    "batch_number": batch_number,
                    "expiry_date": expiry_date,
                    "unit_weight": data.get("unit_weight"),
                    "weight_unit": data.get("weight_unit"),
                }
            ],
            user_id=current_user.id,
            invoice_no=invoice_no,
            purchase_date=get_working_date(),
            notes=f"{'Restock' if action == 'update' else 'Sale quick-add'}: {product.name}",
        )
        log_audit(action, "product", product.id, product.name)
        log_audit("create", "purchase", purchase.id, purchase.invoice_no)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 400

    return jsonify(
        {
            "ok": True,
            "id": product.id,
            "name": product.name,
            "sku": product.sku,
            "sale_price": float(product.sale_price or 0),
            "list_price": float(product.sale_price or 0),
            "stock": float(product.current_stock or 0),
            "stock_display": product.stock_display,
            "unit_weight": float(product.unit_weight) if product.unit_weight is not None else None,
            "weight_unit": product.weight_unit or "",
            "photo_url": product.photo_url,
            "purchase_id": purchase.id,
            "purchase_invoice": purchase.invoice_no,
            "restocked": action == "update",
        }
    )

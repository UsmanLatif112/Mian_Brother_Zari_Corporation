from flask import Blueprint, jsonify, request
from flask_login import login_required
from sqlalchemy import or_
from decimal import Decimal

from app.extensions import db
from app.forms import CustomerForm
from app.models import Category, Customer, ExpenseCategory, Product, Sale, Vendor

api_bp = Blueprint("api", __name__)


@api_bp.route("/search")
@login_required
def global_search():
    from app.services.page_search_service import search_scoped
    from app.utils.search_context import resolve_search_context

    q = (request.args.get("q") or "").strip()
    scope = (request.args.get("scope") or "").strip().lower()
    if not scope:
        scope = resolve_search_context(request.path).scope
    if len(q) < 2:
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

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Customer name is required."}), 400

    joined = data.get("joined_date") or date.today().isoformat()
    try:
        joined_date = date.fromisoformat(joined)
    except ValueError:
        joined_date = date.today()

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

    from app.utils.uploads import accept_uploaded_path

    sku = (data.get("sku") or "").strip() or f"SKU-{Product.query.count() + 1}"
    barcode = (data.get("barcode") or "").strip() or None
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

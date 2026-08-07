from datetime import date
from decimal import Decimal
from urllib.parse import urlparse

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import Customer, Product, Sale
from app.models.sales import PaymentStatus, SaleItem
from app.services.audit_service import log_audit
from app.services.sale_service import create_sale, replace_sale, void_sale
from app.utils.decorators import permission_required
from app.utils.working_date import get_working_date

sales_bp = Blueprint("sales", __name__)


def _parse_date(value):
    if not value:
        return None
    return date.fromisoformat(value)


def _safe_internal_path(raw: str | None) -> str | None:
    """Allow only same-app relative paths (block open redirects)."""
    if not raw:
        return None
    raw = str(raw).strip()
    if not raw:
        return None
    # Absolute URL → keep path+query if host matches this request
    if raw.startswith("http://") or raw.startswith("https://"):
        parsed = urlparse(raw)
        if parsed.netloc and parsed.netloc != request.host:
            return None
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        raw = path
    if not raw.startswith("/") or raw.startswith("//"):
        return None
    # Never bounce back to the invoice itself
    if "/invoice" in raw.split("?")[0]:
        return None
    return raw


def _back_label_for_path(path: str) -> str:
    base = (path or "/").split("?")[0].rstrip("/") or "/"
    rules = (
        ("/sales", "Back to Sales"),
        ("/vendors", "Back to Vendors"),
        ("/customers", "Back to Customers"),
        ("/journal", "Back to Journal"),
        ("/account", "Back to Account"),
        ("/inventory", "Back to Inventory"),
        ("/purchases", "Back to Purchases"),
        ("/expenses", "Back to Expenses"),
        ("/reports", "Back to Reports"),
        ("/sync-backup", "Back to Sync & Backup"),
        ("/auth", "Back"),
        ("/", "Back to Dashboard"),
    )
    for prefix, label in rules:
        if prefix == "/":
            if base == "/" or base == "":
                return label
            continue
        if base == prefix or base.startswith(prefix + "/"):
            return label
    return "Back"


def _invoice_back_target() -> tuple[str, str]:
    """Where the invoice Back button should go, and its label."""
    default_url = url_for("sales.index")
    default_label = "Back to Sales"

    # Explicit ?next=/path preferred when links pass it
    candidate = _safe_internal_path(request.args.get("next"))
    if not candidate:
        candidate = _safe_internal_path(request.referrer)
    if not candidate:
        return default_url, default_label
    return candidate, _back_label_for_path(candidate)


def _infer_item_sale_mode(it) -> str | None:
    """full | open for weighted products; None for piece-only products."""
    product = it.product
    if not product or not product.unit_weight:
        return None
    uw = Decimal(str(product.unit_weight))
    if uw <= 0:
        return None
    qty = Decimal(str(it.quantity or 0))
    sw = it.sale_weight
    if sw is None:
        return "full"
    sw = Decimal(str(sw))
    whole = qty == qty.to_integral_value()
    matches_full = abs(sw - qty * uw) <= Decimal("0.001")
    if whole and matches_full:
        return "full"
    return "open"


def _parse_sale_request(data):
    """Parse JSON sale payload into (payload, items) or (None, error_response)."""
    from app.services.fifo_service import fifo_weight_for_qty, next_fifo_packaging, weight_to_stock_qty_fifo
    from app.utils.weight_utils import (
        normalize_weight_unit,
        parse_unit_weight,
        product_has_weight,
        proportional_sale_amount,
    )

    items_raw = data.get("items") or []
    if not items_raw:
        return None, (jsonify({"ok": False, "error": "Add at least one item."}), 400)

    items = []
    for line in items_raw:
        product = db.session.get(Product, int(line.get("product_id") or 0))
        if not product:
            return None, (jsonify({"ok": False, "error": "Invalid product selected."}), 400)

        list_price = Decimal(
            str(line.get("list_unit_price") if line.get("list_unit_price") is not None else product.sale_price or 0)
        )
        line_discount = Decimal(str(line.get("discount") or 0))
        sale_weight = None
        weight_unit = None
        deduct_unit_weight = None

        fifo_uw, fifo_wu = next_fifo_packaging(product)
        has_weight = (fifo_uw is not None and fifo_uw > 0) or product_has_weight(product)
        if has_weight:
            uw = fifo_uw if fifo_uw is not None else Decimal(str(product.unit_weight))
            weight_unit = (
                fifo_wu
                or product.weight_unit
                or normalize_weight_unit(line.get("weight_unit"))
                or "kg"
            )
            mode = (line.get("sale_mode") or "full").strip().lower()
            if mode not in ("full", "open"):
                raw_w = line.get("sale_weight")
                mode = "open" if raw_w not in (None, "") else "full"

            if mode == "open":
                raw_w = line.get("sale_weight")
                if raw_w in (None, ""):
                    sale_weight = uw
                else:
                    sale_weight = parse_unit_weight(raw_w) or Decimal("0")
                if sale_weight <= 0:
                    return None, (
                        jsonify({"ok": False, "error": f"Invalid sale weight for {product.name}."}),
                        400,
                    )
                try:
                    qty = weight_to_stock_qty_fifo(product, sale_weight)
                except ValueError as exc:
                    return None, (jsonify({"ok": False, "error": str(exc)}), 400)
                from app.services.fifo_service import next_fifo_sale_price

                list_for_open = next_fifo_sale_price(product)
                if line.get("list_unit_price") is not None and str(line.get("list_unit_price")) != "":
                    list_for_open = Decimal(str(line.get("list_unit_price")))
                suggested = proportional_sale_amount(list_for_open, uw, sale_weight)
                if line.get("line_total") is not None and str(line.get("line_total")) != "":
                    line_total = Decimal(str(line.get("line_total")))
                elif line.get("unit_price") is not None and str(line.get("unit_price")) != "":
                    line_total = Decimal(str(line.get("unit_price")))
                else:
                    line_total = suggested
                if line_total < 0:
                    return None, (
                        jsonify({"ok": False, "error": f"Invalid sale price for {product.name}."}),
                        400,
                    )
                line_total = line_total - line_discount
                if line_total < 0:
                    line_total = Decimal("0")
                unit_price = (line_total / qty) if qty else list_for_open
                list_price = list_for_open
                deduct_unit_weight = None
            else:
                qty = Decimal(str(line.get("quantity") or 0))
                if qty <= 0:
                    return None, (
                        jsonify({"ok": False, "error": f"Invalid quantity for {product.name}."}),
                        400,
                    )
                try:
                    sale_weight = fifo_weight_for_qty(product, qty)
                except ValueError as exc:
                    return None, (jsonify({"ok": False, "error": str(exc)}), 400)
                if sale_weight is None:
                    sale_weight = qty * uw
                unit_price = Decimal(
                    str(line.get("unit_price") if line.get("unit_price") is not None else list_price)
                )
                if line.get("line_total") is not None and str(line.get("line_total")) != "":
                    line_total = Decimal(str(line.get("line_total"))) - line_discount
                else:
                    line_total = qty * unit_price - line_discount
                if line_total < 0:
                    line_total = Decimal("0")
                deduct_unit_weight = None
        else:
            qty = Decimal(str(line.get("quantity") or 0))
            if qty <= 0:
                return None, (
                    jsonify({"ok": False, "error": f"Invalid quantity for {product.name}."}),
                    400,
                )
            unit_price = Decimal(str(line.get("unit_price") if line.get("unit_price") is not None else list_price))
            if line.get("line_total") is not None and str(line.get("line_total")) != "":
                line_total = Decimal(str(line.get("line_total"))) - line_discount
            else:
                line_total = qty * unit_price - line_discount
            if line_total < 0:
                line_total = Decimal("0")
            deduct_unit_weight = None
            mode = "qty"

        items.append(
            {
                "product": product,
                "quantity": qty,
                "unit_price": unit_price,
                "list_unit_price": list_price,
                "line_total": line_total,
                "sale_weight": sale_weight,
                "weight_unit": weight_unit,
                "unit_weight": deduct_unit_weight,
                "sale_mode": mode if has_weight else "qty",
                "discount": line_discount,
                "tax_rate": line.get("tax_rate", product.tax_rate or 0),
            }
        )

    sale_date = _parse_date(data.get("sale_date")) or get_working_date()
    payment_status = (data.get("payment_status") or "paid").lower()
    discount = Decimal(str(data.get("discount") or 0))
    if discount < 0:
        discount = Decimal("0")
    subtotal_estimate = sum(Decimal(str(i["line_total"])) for i in items)
    if discount > subtotal_estimate:
        discount = subtotal_estimate
    grand_estimate = subtotal_estimate - discount

    # Allow overpay on partial too (excess → customer advance)
    if payment_status == "unpaid":
        amount_paid = Decimal("0")
        payment_method = "credit"
        needs_customer = True
    elif payment_status == "partial":
        amount_paid = Decimal(str(data.get("amount_paid") or 0))
        payment_method = "cash"
        needs_customer = True
    else:
        # Paid: customer optional (walk-in allowed). Force full settlement unless overpaying.
        raw_paid = data.get("amount_paid")
        if raw_paid in (None, ""):
            amount_paid = grand_estimate
        else:
            amount_paid = Decimal(str(raw_paid))
        if amount_paid < grand_estimate:
            amount_paid = grand_estimate
        payment_method = "cash"
        needs_customer = amount_paid > grand_estimate

    customer_id = data.get("customer_id") or None
    if customer_id:
        customer_id = int(customer_id)

    salesman_id = data.get("salesman_id") or None
    if salesman_id:
        salesman_id = int(salesman_id)

    if needs_customer and not customer_id:
        msg = (
            "Select a customer for overpayment (advance)."
            if payment_status == "paid" and amount_paid > grand_estimate
            else "Select a customer for credit, partial, or overpayment sales."
        )
        return None, (jsonify({"ok": False, "error": msg}), 400)

    payload = {
        "customer_id": customer_id,
        "salesman_id": salesman_id,
        "sale_date": sale_date,
        "discount": discount,
        "tax_amount": data.get("tax_amount", 0),
        "payment_method": payment_method,
        "amount_paid": amount_paid,
        "notes": data.get("notes"),
    }
    return (payload, items), None


def _audit_sale(action, sale):
    import json

    audit_payload = {
        "invoice_no": sale.invoice_no,
        "sale_date": str(sale.sale_date),
        "customer_id": sale.customer_id,
        "customer": sale.customer.name if sale.customer else "Walk-in",
        "subtotal": float(sale.subtotal or 0),
        "discount": float(sale.discount or 0),
        "grand_total": float(sale.grand_total or 0),
        "amount_paid": float(sale.amount_paid or 0),
        "payment_status": sale.payment_status.value if sale.payment_status else None,
        "payment_method": sale.payment_method.value if sale.payment_method else None,
        "notes": sale.notes,
        "items": [
            {
                "product_id": it.product_id,
                "product": it.product.name if it.product else None,
                "quantity": float(it.quantity or 0),
                "unit_price": float(it.unit_price or 0),
                "list_price": float(it.product.sale_price or 0) if it.product else None,
                "line_total": float(it.line_total or 0),
            }
            for it in sale.items
        ],
    }
    log_audit(action, "sale", sale.id, json.dumps(audit_payload, ensure_ascii=False))


def _sale_success(sale):
    return jsonify(
        {
            "ok": True,
            "sale_id": sale.id,
            "invoice_no": sale.invoice_no,
            "invoice_url": url_for("sales.invoice", sale_id=sale.id),
        }
    )


@sales_bp.route("/")
@login_required
@permission_required("sales.view")
def index():
    from app.services.dashboard_service import _range_for_filter

    period = request.args.get("period", "all")
    period_start = _parse_date(request.args.get("start_date"))
    period_end = _parse_date(request.args.get("end_date"))
    range_start, range_end = _range_for_filter(period, period_start, period_end)

    query = Sale.query.options(
        joinedload(Sale.customer),
        joinedload(Sale.salesman),
        joinedload(Sale.items).joinedload(SaleItem.product),
    )
    if range_start:
        query = query.filter(Sale.sale_date >= range_start)
    if range_end:
        query = query.filter(Sale.sale_date <= range_end)

    sales = query.order_by(Sale.sale_date.desc(), Sale.id.desc()).limit(200).all()

    from app.services.journal_service import items_particulars

    for s in sales:
        s.particulars = items_particulars(s.items, fallback="—", kind="sale")

    total_sale_q = db.session.query(func.coalesce(func.sum(Sale.grand_total), 0))
    from sqlalchemy import case

    total_credit_q = db.session.query(
        func.coalesce(
            func.sum(
                case(
                    (Sale.amount_paid < Sale.grand_total, Sale.grand_total - Sale.amount_paid),
                    else_=0,
                )
            ),
            0,
        )
    ).filter(Sale.payment_status != PaymentStatus.PAID)
    if range_start:
        total_sale_q = total_sale_q.filter(Sale.sale_date >= range_start)
        total_credit_q = total_credit_q.filter(Sale.sale_date >= range_start)
    if range_end:
        total_sale_q = total_sale_q.filter(Sale.sale_date <= range_end)
        total_credit_q = total_credit_q.filter(Sale.sale_date <= range_end)

    total_sale = total_sale_q.scalar() or Decimal("0")
    total_credit = total_credit_q.scalar() or Decimal("0")

    customers = Customer.query.filter_by(is_deleted=False).order_by(Customer.name).all()
    products = Product.query.filter_by(is_deleted=False).order_by(Product.name).all()

    from app.models import Category
    from app.services.fifo_service import next_fifo_sale_price

    categories = Category.query.filter_by(is_deleted=False, parent_id=None).order_by(Category.name).all()
    subcategories = (
        Category.query.filter(Category.parent_id.isnot(None), Category.is_deleted.is_(False))
        .order_by(Category.name)
        .all()
    )

    open_modal = bool(session.pop("open_sale_modal", False))

    return render_template(
        "sales/index.html",
        sales=sales,
        total_sale=total_sale,
        total_credit=total_credit,
        selected_period=period,
        start_date=request.args.get("start_date") or "",
        end_date=request.args.get("end_date") or "",
        customers=customers,
        products=[
            {
                "id": p.id,
                "name": p.name,
                "sku": p.sku,
                "sale_price": float(next_fifo_sale_price(p)),
                "photo_url": p.photo_url,
            }
            for p in products
        ],
        categories=categories,
        subcategories=subcategories,
        today=get_working_date().isoformat(),
        open_modal=open_modal,
    )


@sales_bp.route("/create", methods=["POST"])
@login_required
@permission_required("sales.create")
def create():
    """AJAX create sale from modal."""
    data = request.get_json(silent=True) or {}
    parsed, err = _parse_sale_request(data)
    if err:
        return err
    payload, items = parsed
    try:
        sale = create_sale(payload, items, current_user.id)
        _audit_sale("create", sale)
        db.session.commit()
        return _sale_success(sale)
    except ValueError as exc:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 400


@sales_bp.route("/pos")
@login_required
@permission_required("sales.create")
def pos():
    session["open_sale_modal"] = True
    return redirect(url_for("sales.index"))


@sales_bp.route("/pos/submit", methods=["POST"])
@login_required
@permission_required("sales.create")
def pos_submit():
    return create()


def _item_unit_weight(it):
    """Best packaging weight for a sold line (from sale weight / qty when possible)."""
    qty = Decimal(str(it.quantity or 0))
    sw = it.sale_weight
    if sw is not None and qty > 0:
        # Full-bag sales: sale_weight ≈ qty × unit_weight
        inferred = (Decimal(str(sw)) / qty).quantize(Decimal("0.001"))
        if inferred > 0:
            return float(inferred)
    if it.product and it.product.unit_weight is not None:
        return float(it.product.unit_weight)
    return None


@sales_bp.route("/<int:sale_id>")
@login_required
@permission_required("sales.view")
def get_sale(sale_id):
    """JSON payload to load a sale into the Add Sale modal for editing."""
    sale = (
        Sale.query.options(
            joinedload(Sale.customer),
            joinedload(Sale.salesman),
            joinedload(Sale.items).joinedload(SaleItem.product),
        )
        .filter_by(id=sale_id)
        .first()
    )
    if not sale:
        return jsonify({"ok": False, "error": "Sale not found."}), 404

    status = sale.payment_status.value if sale.payment_status else "paid"
    amount_paid = float(sale.amount_paid or 0)

    return jsonify(
        {
            "ok": True,
            "sale": {
                "id": sale.id,
                "invoice_no": sale.invoice_no,
                "sale_date": sale.sale_date.isoformat() if sale.sale_date else None,
                "customer_id": sale.customer_id,
                "customer_name": sale.customer.name if sale.customer else "Walk-in",
                "salesman_id": sale.salesman_id,
                "salesman_name": sale.salesman.name if sale.salesman else "",
                "payment_status": status,
                "amount_paid": amount_paid,
                "discount": float(sale.discount or 0),
                "notes": sale.notes or "",
                "items": [
                    {
                        "product_id": it.product_id,
                        "name": it.product.name if it.product else "Item",
                        "quantity": float(it.quantity or 0),
                        "unit_price": float(it.unit_price or 0),
                        "list_unit_price": float(
                            it.list_unit_price
                            if it.list_unit_price is not None
                            else (it.product.sale_price if it.product else 0) or 0
                        ),
                        "line_total": float(it.line_total or 0),
                        "sale_weight": float(it.sale_weight) if it.sale_weight is not None else None,
                        "weight_unit": it.weight_unit
                        or (it.product.weight_unit if it.product else None),
                        "unit_weight": _item_unit_weight(it),
                        "sale_mode": _infer_item_sale_mode(it),
                        "photo_url": it.product.photo_url if it.product else None,
                    }
                    for it in sale.items
                ],
            },
        }
    )


@sales_bp.route("/<int:sale_id>/replace", methods=["POST"])
@login_required
@permission_required("sales.*")
def replace(sale_id):
    """Fully replace sale contents (same invoice number) from Add Sale modal."""
    data = request.get_json(silent=True) or {}
    parsed, err = _parse_sale_request(data)
    if err:
        return err
    payload, items = parsed
    try:
        sale = replace_sale(sale_id, payload, items, current_user.id)
        _audit_sale("update", sale)
        db.session.commit()
        return _sale_success(sale)
    except ValueError as exc:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 400


@sales_bp.route("/<int:sale_id>/invoice")
@login_required
@permission_required("sales.view")
def invoice(sale_id):
    from app.services.settings_service import get_business_info

    sale = db.session.get(Sale, sale_id)
    if not sale:
        flash("Sale not found.", "danger")
        return redirect(url_for("sales.index"))
    back_url, back_label = _invoice_back_target()
    business = get_business_info()
    # Staff invoices show their company — never fall back to product branding
    if current_user.is_authenticated and not current_user.is_super_admin():
        company = (getattr(current_user, "company_name", None) or "").strip()
        if company:
            business = {**business, "company_name": company}
    return render_template(
        "sales/invoice.html",
        sale=sale,
        business=business,
        back_url=back_url,
        back_label=back_label,
    )


@sales_bp.route("/<int:sale_id>/delete", methods=["POST"])
@login_required
@permission_required("sales.*")
def delete(sale_id):
    try:
        void_sale(sale_id, current_user.id)
        db.session.commit()
        flash("Sale deleted. Stock, cash and ledger reversed.", "success")
    except (ValueError, TypeError) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("sales.index"))

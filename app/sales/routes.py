from datetime import date
from decimal import Decimal

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


def _parse_sale_request(data):
    """Parse JSON sale payload into (payload, items) or (None, error_response)."""
    items_raw = data.get("items") or []
    if not items_raw:
        return None, (jsonify({"ok": False, "error": "Add at least one item."}), 400)

    items = []
    for line in items_raw:
        product = db.session.get(Product, int(line.get("product_id") or 0))
        if not product:
            return None, (jsonify({"ok": False, "error": "Invalid product selected."}), 400)
        qty = Decimal(str(line.get("quantity") or 0))
        if qty <= 0:
            return None, (
                jsonify({"ok": False, "error": f"Invalid quantity for {product.name}."}),
                400,
            )
        items.append(
            {
                "product": product,
                "quantity": qty,
                "unit_price": line.get("unit_price", product.sale_price),
                "discount": line.get("discount", 0),
                "tax_rate": line.get("tax_rate", product.tax_rate or 0),
            }
        )

    sale_date = _parse_date(data.get("sale_date")) or get_working_date()
    payment_status = (data.get("payment_status") or "paid").lower()
    discount = Decimal(str(data.get("discount") or 0))
    if discount < 0:
        discount = Decimal("0")
    subtotal_estimate = sum(
        Decimal(str(i["quantity"])) * Decimal(str(i["unit_price"])) for i in items
    )
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

    if needs_customer and not customer_id:
        msg = (
            "Select a customer for overpayment (advance)."
            if payment_status == "paid" and amount_paid > grand_estimate
            else "Select a customer for credit, partial, or overpayment sales."
        )
        return None, (jsonify({"ok": False, "error": msg}), 400)

    payload = {
        "customer_id": customer_id,
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
        joinedload(Sale.items).joinedload(SaleItem.product),
    )
    if range_start:
        query = query.filter(Sale.sale_date >= range_start)
    if range_end:
        query = query.filter(Sale.sale_date <= range_end)

    sales = query.order_by(Sale.sale_date.desc(), Sale.id.desc()).limit(200).all()

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


@sales_bp.route("/<int:sale_id>")
@login_required
@permission_required("sales.view")
def get_sale(sale_id):
    """JSON payload to load a sale into the Add Sale modal for editing."""
    sale = (
        Sale.query.options(
            joinedload(Sale.customer),
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
    return render_template(
        "sales/invoice.html",
        sale=sale,
        business=get_business_info(),
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

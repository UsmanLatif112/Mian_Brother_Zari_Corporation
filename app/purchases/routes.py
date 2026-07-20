from datetime import date
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import Product, Purchase, PurchaseItem, Vendor
from app.services.audit_service import log_audit
from app.services.fifo_service import fifo_receive
from app.utils.decorators import permission_required

purchases_bp = Blueprint("purchases", __name__)


def _purchase_page(open_modal=False):
    purchases = Purchase.query.order_by(Purchase.purchase_date.desc()).limit(100).all()
    vendors = Vendor.query.filter_by(is_deleted=False).order_by(Vendor.name).all()
    products = Product.query.filter_by(is_deleted=False).order_by(Product.name).all()
    return render_template(
        "purchases/index.html",
        purchases=purchases,
        vendors=vendors,
        products=products,
        today=date.today().isoformat(),
        open_modal=open_modal or request.args.get("open_modal") == "1",
    )


@purchases_bp.route("/")
@login_required
@permission_required("purchases.view")
def index():
    return _purchase_page()


@purchases_bp.route("/create", methods=["GET", "POST"])
@login_required
@permission_required("purchases.create")
def create():
    if request.method == "GET":
        return redirect(url_for("purchases.index", open_modal=1))
    try:
        purchase = Purchase(
            invoice_no=request.form["invoice_no"],
            vendor_id=int(request.form["vendor_id"]),
            purchase_date=date.fromisoformat(request.form["purchase_date"]),
            discount=Decimal(request.form.get("discount") or 0),
            tax_amount=Decimal(request.form.get("tax_amount") or 0),
            transport_charges=Decimal(request.form.get("transport_charges") or 0),
            notes=request.form.get("notes"),
            created_by_id=current_user.id,
        )
        db.session.add(purchase)
        db.session.flush()
        subtotal = Decimal("0")
        product_ids = request.form.getlist("product_id")
        if not product_ids:
            raise ValueError("Add at least one product line.")
        sale_prices = request.form.getlist("sale_price")
        for i, pid in enumerate(product_ids):
            qty = Decimal(request.form.getlist("quantity")[i])
            price = Decimal(request.form.getlist("unit_price")[i])
            product = db.session.get(Product, int(pid))
            if not product:
                raise ValueError("Invalid product on purchase line.")
            # Optional per-batch sale price; falls back to product default
            sale_raw = sale_prices[i] if i < len(sale_prices) else ""
            if sale_raw not in (None, ""):
                batch_sale = Decimal(sale_raw)
            else:
                batch_sale = Decimal(str(product.sale_price or 0))
            line_total = qty * price
            item = PurchaseItem(
                purchase_id=purchase.id,
                product_id=int(pid),
                quantity=qty,
                unit_price=price,
                line_total=line_total,
            )
            db.session.add(item)
            fifo_receive(
                product,
                qty,
                price,
                "purchase",
                purchase.id,
                current_user.id,
                sale_price=batch_sale,
            )
            subtotal += line_total
        purchase.subtotal = subtotal
        purchase.grand_total = (
            subtotal - purchase.discount + purchase.tax_amount + purchase.transport_charges
        )
        vendor = db.session.get(Vendor, purchase.vendor_id)
        vendor.balance += purchase.grand_total
        log_audit("create", "purchase", purchase.id)
        db.session.commit()
        flash("Purchase recorded.", "success")
        return redirect(url_for("purchases.index"))
    except Exception as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return _purchase_page(open_modal=True)

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, inspect, text

from app.extensions import db
from app.models import (
    Customer,
    Expense,
    Product,
    Purchase,
    Sale,
    Vendor,
)
from app.models.sales import PaymentStatus, SaleItem
from app.services.cashbook_service import get_cash_dashboard_metrics, period_cash_collections
from app.services.fifo_service import stock_valuation
from app.services.account_service import account_previous_amount

# Customer payment / credit types and allowed due days
CUSTOMER_TYPE_META = {
    "good": {"label": "Good", "due_days": 30},
    "bad": {"label": "Bad", "due_days": 0},
    "1_year": {"label": "1 Year", "due_days": 365},
    "6_month": {"label": "6 Month", "due_days": 180},
    "late_pay": {"label": "Late Pay", "due_days": 0},
}


def ensure_customer_type_column():
    """Add missing customers / receiving / stock_layer columns (offline-safe)."""
    try:
        insp = inspect(db.engine)
        cols = {c["name"] for c in insp.get_columns("customers")}
        alters = []
        if "customer_type" not in cols:
            alters.append(
                "ALTER TABLE customers ADD COLUMN customer_type "
                "VARCHAR(30) DEFAULT 'good' NOT NULL"
            )
        if "old_book_no" not in cols:
            alters.append("ALTER TABLE customers ADD COLUMN old_book_no VARCHAR(50)")
        if "joined_date" not in cols:
            alters.append("ALTER TABLE customers ADD COLUMN joined_date DATE")
        if alters:
            with db.engine.begin() as conn:
                for stmt in alters:
                    conn.execute(text(stmt))
    except Exception:
        pass

    try:
        insp = inspect(db.engine)
        recv_cols = {c["name"] for c in insp.get_columns("customer_receivings")}
        if "payment_type" not in recv_cols:
            with db.engine.begin() as conn:
                conn.execute(
                    text(
                        "ALTER TABLE customer_receivings ADD COLUMN payment_type "
                        "VARCHAR(30) DEFAULT 'account_settle' NOT NULL"
                    )
                )
    except Exception:
        pass

    try:
        insp = inspect(db.engine)
        vp_cols = {c["name"] for c in insp.get_columns("vendor_payments")}
        if "payment_type" not in vp_cols:
            with db.engine.begin() as conn:
                conn.execute(
                    text(
                        "ALTER TABLE vendor_payments ADD COLUMN payment_type "
                        "VARCHAR(30) DEFAULT 'account_settle' NOT NULL"
                    )
                )
    except Exception:
        pass

    try:
        insp = inspect(db.engine)
        layer_cols = {c["name"] for c in insp.get_columns("stock_layers")}
        alters = []
        if "sale_price" not in layer_cols:
            alters.append("ALTER TABLE stock_layers ADD COLUMN sale_price NUMERIC(14, 2)")
        if "notes" not in layer_cols:
            alters.append("ALTER TABLE stock_layers ADD COLUMN notes TEXT")
        if "batch_number" not in layer_cols:
            alters.append("ALTER TABLE stock_layers ADD COLUMN batch_number VARCHAR(80)")
        if "expiry_date" not in layer_cols:
            alters.append("ALTER TABLE stock_layers ADD COLUMN expiry_date DATE")
        if "vendor_id" not in layer_cols:
            alters.append("ALTER TABLE stock_layers ADD COLUMN vendor_id INTEGER")
        if "invoice_no" not in layer_cols:
            alters.append("ALTER TABLE stock_layers ADD COLUMN invoice_no VARCHAR(80)")
        if "quantity_received" not in layer_cols:
            alters.append(
                "ALTER TABLE stock_layers ADD COLUMN quantity_received NUMERIC(14, 3)"
            )
        if alters:
            with db.engine.begin() as conn:
                for stmt in alters:
                    conn.execute(text(stmt))
                # Backfill only when columns were just added (avoid write locks every request)
                conn.execute(
                    text(
                        "UPDATE stock_layers SET quantity_received = quantity_remaining "
                        "WHERE quantity_received IS NULL"
                    )
                )
                conn.execute(
                    text(
                        "UPDATE stock_layers SET sale_price = ("
                        "SELECT sale_price FROM products WHERE products.id = stock_layers.product_id"
                        ") WHERE sale_price IS NULL"
                    )
                )
    except Exception:
        pass

    # Optional photos on customers / vendors / products / sale_items
    for table, col, coltype in (
        ("customers", "photo", "VARCHAR(255)"),
        ("vendors", "photo", "VARCHAR(255)"),
        ("products", "photo", "VARCHAR(255)"),
        ("sale_items", "photo", "VARCHAR(255)"),
    ):
        try:
            insp = inspect(db.engine)
            cols = {c["name"] for c in insp.get_columns(table)}
            if col not in cols:
                with db.engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}"))
        except Exception:
            pass

    # Product unit weight + sale-line weight fields (partial-weight sales)
    for table, col, coltype in (
        ("products", "unit_weight", "NUMERIC(14, 3)"),
        ("products", "weight_unit", "VARCHAR(10)"),
        ("sale_items", "sale_weight", "NUMERIC(14, 3)"),
        ("sale_items", "weight_unit", "VARCHAR(10)"),
        ("sale_items", "list_unit_price", "NUMERIC(14, 2)"),
    ):
        try:
            insp = inspect(db.engine)
            cols = {c["name"] for c in insp.get_columns(table)}
            if col not in cols:
                with db.engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}"))
        except Exception:
            pass


def _range_for_filter(period: str, start_date=None, end_date=None):
    # Anchor periods to the selected working date so "Today" matches posting day.
    from app.utils.working_date import get_working_date

    today = get_working_date()
    if period == "today":
        return today, today
    if period == "week":
        return today - timedelta(days=6), today
    if period == "month":
        return today - timedelta(days=29), today
    if period == "3_month":
        return today - timedelta(days=89), today
    if period == "6_month":
        return today - timedelta(days=179), today
    if period == "12_month":
        return today - timedelta(days=364), today
    if period == "custom" and start_date and end_date:
        return start_date, end_date
    return None, None


def _sum_period(amount_field, date_field, start=None, end=None):
    q = db.session.query(func.coalesce(func.sum(amount_field), 0))
    if start and end:
        q = q.filter(date_field >= start, date_field <= end)
    return q.scalar() or Decimal("0")


def _filters(date_field, start, end):
    if start and end:
        return [date_field >= start, date_field <= end]
    return []


def _customer_due_info(customer: Customer, today: date, oldest_sale_date=None, *, batched=False):
    ctype = getattr(customer, "customer_type", None) or "good"
    meta = CUSTOMER_TYPE_META.get(ctype, CUSTOMER_TYPE_META["good"])
    allowed = meta["due_days"]
    if not batched and oldest_sale_date is None:
        oldest = (
            Sale.query.filter(
                Sale.customer_id == customer.id,
                Sale.payment_status != PaymentStatus.PAID,
            )
            .order_by(Sale.sale_date.asc())
            .first()
        )
        if not oldest:
            oldest = (
                Sale.query.filter_by(customer_id=customer.id)
                .order_by(Sale.sale_date.desc())
                .first()
            )
        oldest_sale_date = oldest.sale_date if oldest else None
    if not oldest_sale_date:
        return {
            "type": ctype,
            "type_label": meta["label"],
            "allowed_days": allowed,
            "due_days": 0,
            "overdue_days": 0,
        }
    age = (today - oldest_sale_date).days
    overdue = max(0, age - allowed) if allowed else age
    return {
        "type": ctype,
        "type_label": meta["label"],
        "allowed_days": allowed,
        "due_days": age,
        "overdue_days": overdue,
    }


def _oldest_unpaid_sale_dates(customer_ids):
    """One query: oldest unpaid sale date per customer."""
    if not customer_ids:
        return {}
    rows = (
        db.session.query(Sale.customer_id, func.min(Sale.sale_date))
        .filter(
            Sale.customer_id.in_(customer_ids),
            Sale.payment_status != PaymentStatus.PAID,
        )
        .group_by(Sale.customer_id)
        .all()
    )
    return {cid: d for cid, d in rows}


def get_dashboard_metrics(period="all", start_date=None, end_date=None):
    # Schema ensure runs once at app startup — not on every dashboard hit
    from app.utils.working_date import get_working_date

    start, end = _range_for_filter(period, start_date, end_date)
    today = get_working_date()

    total_sale = _sum_period(Sale.grand_total, Sale.sale_date, start, end)
    total_purchasing = _sum_period(Purchase.grand_total, Purchase.purchase_date, start, end)
    expense_q = db.session.query(func.coalesce(func.sum(Expense.amount), 0)).filter(
        Expense.is_deleted.is_(False)
    )
    if start and end:
        expense_q = expense_q.filter(Expense.expense_date >= start, Expense.expense_date <= end)
    total_expense = expense_q.scalar() or Decimal("0")

    cost_q = db.session.query(func.coalesce(func.sum(SaleItem.cost_of_goods), 0)).join(
        Sale, Sale.id == SaleItem.sale_id
    )
    for f in _filters(Sale.sale_date, start, end):
        cost_q = cost_q.filter(f)
    total_cost = cost_q.scalar() or Decimal("0")

    gross_profit = total_sale - total_cost
    net_profit = gross_profit - total_expense
    total_credit = (
        db.session.query(func.coalesce(func.sum(Customer.balance), 0))
        .filter(Customer.is_deleted.is_(False), Customer.balance > 0)
        .scalar()
    ) or Decimal("0")
    cash_collections = period_cash_collections(start, end)
    cash_metrics = get_cash_dashboard_metrics(
        total_sale=cash_collections,
        previous_amount=account_previous_amount(),
        total_expense=total_expense,
    )
    previous_balance = cash_metrics["previous_balance"]
    cash_in_hand = cash_metrics["cash_in_hand"]
    cash_without_expense = cash_metrics["cash_without_expense"]
    cash_without_prev_and_expense = cash_metrics["cash_without_prev_and_expense"]
    stock_value = stock_valuation()

    # Chart series (aligned labels)
    group_fmt = "%Y-%m-%d" if start and end and (end - start).days <= 31 else "%Y-%m"

    sales_income = {
        r[0]: float(r[1] or 0)
        for r in db.session.query(
            func.strftime(group_fmt, Sale.sale_date),
            func.coalesce(func.sum(Sale.grand_total), 0),
        )
        .filter(*_filters(Sale.sale_date, start, end))
        .group_by(func.strftime(group_fmt, Sale.sale_date))
        .all()
    }
    sales_cost = {
        r[0]: float(r[1] or 0)
        for r in db.session.query(
            func.strftime(group_fmt, Sale.sale_date),
            func.coalesce(func.sum(SaleItem.cost_of_goods), 0),
        )
        .join(Sale, Sale.id == SaleItem.sale_id)
        .filter(*_filters(Sale.sale_date, start, end))
        .group_by(func.strftime(group_fmt, Sale.sale_date))
        .all()
    }
    expense_map = {
        r[0]: float(r[1] or 0)
        for r in db.session.query(
            func.strftime(group_fmt, Expense.expense_date),
            func.coalesce(func.sum(Expense.amount), 0),
        )
        .filter(Expense.is_deleted.is_(False), *_filters(Expense.expense_date, start, end))
        .group_by(func.strftime(group_fmt, Expense.expense_date))
        .all()
    }

    all_buckets = sorted(set(sales_income) | set(sales_cost) | set(expense_map))
    chart_labels = []
    chart_income = []
    chart_cost = []
    chart_expense = []
    chart_gross = []
    chart_net = []
    for bucket in all_buckets[-24:]:
        income_f = sales_income.get(bucket, 0.0)
        cost_f = sales_cost.get(bucket, 0.0)
        exp_f = expense_map.get(bucket, 0.0)
        gross_f = income_f - cost_f
        net_f = gross_f - exp_f
        chart_labels.append(bucket)
        chart_income.append(income_f)
        chart_cost.append(cost_f)
        chart_expense.append(exp_f)
        chart_gross.append(gross_f)
        chart_net.append(net_f)

    recent_expenses = (
        Expense.query.filter_by(is_deleted=False)
        .order_by(Expense.expense_date.desc(), Expense.id.desc())
        .limit(5)
        .all()
    )

    credit_customers = (
        Customer.query.filter(
            Customer.is_deleted.is_(False),
            Customer.balance > 0,
        )
        .order_by(Customer.balance.desc())
        .limit(5)
        .all()
    )
    oldest_dates = _oldest_unpaid_sale_dates([c.id for c in credit_customers])
    top_credit = []
    for c in credit_customers:
        due = _customer_due_info(
            c, today, oldest_sale_date=oldest_dates.get(c.id), batched=True
        )
        top_credit.append(
            {
                "id": c.id,
                "name": c.name,
                "balance": c.balance,
                "type": due["type"],
                "type_label": due["type_label"],
                "due_days": due["due_days"],
                "overdue_days": due["overdue_days"],
                "allowed_days": due["allowed_days"],
            }
        )

    top_vendors = (
        Vendor.query.filter(Vendor.is_deleted.is_(False), Vendor.balance > 0)
        .order_by(Vendor.balance.desc())
        .limit(5)
        .all()
    )

    low_stock = (
        Product.query.filter(
            Product.is_deleted.is_(False),
            Product.current_stock <= Product.minimum_stock,
        )
        .order_by(Product.current_stock.asc())
        .limit(5)
        .all()
    )

    # Mini history tables for KPI redirect cards
    recent_sales = (
        Sale.query.order_by(Sale.sale_date.desc(), Sale.id.desc()).limit(5).all()
    )
    recent_purchases = (
        Purchase.query.order_by(Purchase.purchase_date.desc(), Purchase.id.desc())
        .limit(5)
        .all()
    )

    return {
        "total_sale": total_sale,
        "total_cost": total_cost,
        "gross_profit": gross_profit,
        "total_expense": total_expense,
        "net_profit": net_profit,
        "total_credit": total_credit,
        "cash_without_prev_and_expense": cash_without_prev_and_expense,
        "previous_balance": previous_balance,
        "cash_without_expense": cash_without_expense,
        "cash_in_hand": cash_in_hand,
        "stock_value": stock_value,
        "total_purchasing": total_purchasing,
        "chart": {
            "labels": chart_labels,
            "income": chart_income,
            "cost": chart_cost,
            "expense": chart_expense,
            "gross": chart_gross,
            "net": chart_net,
        },
        "recent_expenses": recent_expenses,
        "top_credit": top_credit,
        "top_vendors": top_vendors,
        "low_stock": low_stock,
        "recent_sales": recent_sales,
        "recent_purchases": recent_purchases,
        "selected_period": period,
        "start_date": start,
        "end_date": end,
    }

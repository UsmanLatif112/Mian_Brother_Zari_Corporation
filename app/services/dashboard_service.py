from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, inspect, text

from app.extensions import db
from app.models import (
    Customer,
    Expense,
    ExpenseCategory,
    Product,
    Purchase,
    Sale,
    Vendor,
)
from app.models.sales import PaymentStatus, SaleItem, SaleReturn, SaleReturnItem
from app.services.cashbook_service import get_cash_dashboard_metrics, period_cash_collections
from app.services.fifo_service import stock_valuation, stock_valuation_as_of
from app.services.ledger_service import sum_party_balances_as_of
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
        from app.models import Setting
        from app.version import APP_BUILD

        row = Setting.query.filter_by(key="schema_migrations_build").first()
        if row and str(row.value or "") == str(APP_BUILD):
            return
    except Exception:
        pass

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

    for table in ("customers", "vendors", "products"):
        try:
            insp = inspect(db.engine)
            cols = {c["name"] for c in insp.get_columns(table)}
            if "is_active" not in cols:
                with db.engine.begin() as conn:
                    conn.execute(
                        text(
                            f"ALTER TABLE {table} ADD COLUMN is_active "
                            "BOOLEAN DEFAULT 1 NOT NULL"
                        )
                    )
        except Exception:
            pass

    for table in ("customers", "vendors"):
        try:
            insp = inspect(db.engine)
            cols = {c["name"] for c in insp.get_columns(table)}
            if "cnic" not in cols:
                with db.engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN cnic VARCHAR(20)"))
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
        if "unit_weight" not in layer_cols:
            alters.append("ALTER TABLE stock_layers ADD COLUMN unit_weight NUMERIC(14, 3)")
        if "weight_unit" not in layer_cols:
            alters.append("ALTER TABLE stock_layers ADD COLUMN weight_unit VARCHAR(10)")
        if "open_weight_remaining" not in layer_cols:
            alters.append(
                "ALTER TABLE stock_layers ADD COLUMN open_weight_remaining NUMERIC(14, 3) DEFAULT 0"
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
                # Copy product packaging onto existing batches once (per-batch weight going forward)
                if any("unit_weight" in a for a in alters):
                    conn.execute(
                        text(
                            "UPDATE stock_layers SET unit_weight = ("
                            "SELECT unit_weight FROM products WHERE products.id = stock_layers.product_id"
                            "), weight_unit = ("
                            "SELECT weight_unit FROM products WHERE products.id = stock_layers.product_id"
                            ") WHERE unit_weight IS NULL"
                        )
                    )
                if any("open_weight_remaining" in a for a in alters):
                    conn.execute(
                        text(
                            "UPDATE stock_layers SET open_weight_remaining = 0 "
                            "WHERE open_weight_remaining IS NULL"
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
        ("purchase_items", "unit_weight", "NUMERIC(14, 3)"),
        ("purchase_items", "weight_unit", "VARCHAR(10)"),
    ):
        try:
            insp = inspect(db.engine)
            cols = {c["name"] for c in insp.get_columns(table)}
            if col not in cols:
                with db.engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}"))
        except Exception:
            pass

    try:
        _backfill_purchase_item_weights()
    except Exception:
        pass

    try:
        _migrate_category_name_unique()
    except Exception:
        pass

    try:
        _migrate_fractional_bags_to_open_weight()
    except Exception:
        pass

    try:
        _migrate_qty_precision()
    except Exception:
        pass

    try:
        _ensure_salesman_schema()
    except Exception:
        pass

    try:
        _ensure_sale_return_schema()
    except Exception:
        pass

    try:
        _ensure_inventory_loss_schema()
    except Exception:
        pass

    try:
        _migrate_void_movement_types()
    except Exception:
        pass

    try:
        from app.services.settings_service import set_setting
        from app.version import APP_BUILD

        set_setting("schema_migrations_build", str(APP_BUILD))
    except Exception:
        pass


def _migrate_void_movement_types():
    """Relabel legacy void stock restores (not customer sale returns)."""
    try:
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE stock_movements SET movement_type = 'sale_void_in', "
                    "reference_type = 'sale_void' "
                    "WHERE movement_type = 'sale_return_in' "
                    "AND LOWER(COALESCE(notes, '')) LIKE 'void sale%' "
                    "AND COALESCE(reference_type, '') != 'sale_return'"
                )
            )
            conn.execute(
                text(
                    "UPDATE stock_movements SET movement_type = 'sale_void_in' "
                    "WHERE movement_type = 'sale_return_in' "
                    "AND reference_type = 'sale_void'"
                )
            )
    except Exception:
        pass


def _ensure_sale_return_schema():
    """Create sale_returns / sale_return_items for existing databases."""
    from app.models import SaleReturn, SaleReturnItem

    try:
        SaleReturn.__table__.create(db.engine, checkfirst=True)
    except Exception:
        pass
    try:
        SaleReturnItem.__table__.create(db.engine, checkfirst=True)
    except Exception:
        pass
    # Relabel older return stock movements that were stored as purchase_in
    try:
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE stock_movements SET movement_type = 'sale_return_in' "
                    "WHERE reference_type = 'sale_return' AND movement_type = 'purchase_in'"
                )
            )
    except Exception:
        pass


def _ensure_inventory_loss_schema():
    """Create inventory_losses / sale_item_backorders for existing databases."""
    from app.models import InventoryLoss, SaleItemBackorder

    try:
        InventoryLoss.__table__.create(db.engine, checkfirst=True)
    except Exception:
        pass
    try:
        SaleItemBackorder.__table__.create(db.engine, checkfirst=True)
    except Exception:
        pass


def _ensure_salesman_schema():
    """Create salesmen table and sales.salesman_id for existing databases."""
    from app.models import Salesman

    try:
        Salesman.__table__.create(db.engine, checkfirst=True)
    except Exception:
        pass
    try:
        insp = inspect(db.engine)
        cols = {c["name"] for c in insp.get_columns("sales")}
        if "salesman_id" not in cols:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE sales ADD COLUMN salesman_id INTEGER"))
    except Exception:
        pass


def _migrate_qty_precision():
    """Widen bag-fraction qty columns so 10÷30 kg does not store as 0.333 → 9.99 kg."""
    dialect = db.engine.dialect.name
    if dialect not in ("postgresql", "postgres"):
        # SQLite ignores numeric scale; SQLAlchemy model scale handles new writes.
        return
    alters = (
        ("stock_movements", "quantity", "NUMERIC(18, 6)"),
        ("stock_movements", "balance_after", "NUMERIC(18, 6)"),
        ("sale_items", "quantity", "NUMERIC(18, 6)"),
        ("products", "current_stock", "NUMERIC(18, 6)"),
    )
    for table, col, coltype in alters:
        try:
            with db.engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN {col} TYPE {coltype}"))
        except Exception:
            pass


def _migrate_category_name_unique():
    """
    Drop global UNIQUE on categories.name so names like PGR work freely,
    soft-deleted names can be reused, and subcategories can share labels.
    """
    dialect = db.engine.dialect.name
    insp = inspect(db.engine)
    try:
        cols = {c["name"] for c in insp.get_columns("categories")}
    except Exception:
        return
    if "name" not in cols:
        return

    if dialect == "sqlite":
        create_sql = ""
        try:
            with db.engine.connect() as conn:
                row = conn.execute(
                    text("SELECT sql FROM sqlite_master WHERE type='table' AND name='categories'")
                ).fetchone()
            create_sql = (row[0] or "") if row else ""
        except Exception:
            return
        # Only migrate when name itself is UNIQUE (legacy schema)
        lowered = " ".join(create_sql.lower().split())
        if "name varchar" not in lowered and "name text" not in lowered:
            # still allow name VARCHAR(100) not null unique
            pass
        if "unique" not in lowered:
            return
        # Match patterns like: name VARCHAR(100) NOT NULL UNIQUE
        import re

        if not re.search(r"name\s+varchar\(\d+\)[^,]*unique", lowered):
            # Also: UNIQUE(name) table constraint
            if "unique(name)" not in lowered and "unique (name)" not in lowered:
                return

        with db.engine.begin() as conn:
            conn.execute(text("PRAGMA foreign_keys=OFF"))
            conn.execute(text("DROP TABLE IF EXISTS categories_name_mig"))
            conn.execute(
                text(
                    """
                    CREATE TABLE categories_name_mig (
                        id INTEGER NOT NULL PRIMARY KEY,
                        name VARCHAR(200) NOT NULL,
                        description TEXT,
                        parent_id INTEGER,
                        remote_id INTEGER,
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL,
                        sync_updated_at DATETIME,
                        is_deleted BOOLEAN NOT NULL DEFAULT 0,
                        deleted_at DATETIME,
                        FOREIGN KEY(parent_id) REFERENCES categories_name_mig (id)
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    INSERT INTO categories_name_mig (
                        id, name, description, parent_id, remote_id,
                        created_at, updated_at, sync_updated_at, is_deleted, deleted_at
                    )
                    SELECT
                        id, name, description, parent_id, remote_id,
                        created_at, updated_at, sync_updated_at, is_deleted, deleted_at
                    FROM categories
                    """
                )
            )
            conn.execute(text("DROP TABLE categories"))
            conn.execute(text("ALTER TABLE categories_name_mig RENAME TO categories"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_categories_name ON categories (name)"))
            conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_categories_is_deleted ON categories (is_deleted)")
            )
            conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_categories_remote_id ON categories (remote_id)")
            )
            conn.execute(text("PRAGMA foreign_keys=ON"))
        return

    if dialect in ("mysql", "mariadb"):
        # Drop unique index on name if present
        try:
            with db.engine.begin() as conn:
                rows = conn.execute(text("SHOW INDEX FROM categories WHERE Column_name='name'")).fetchall()
                for row in rows:
                    # Row mapping varies; Non_unique == 0 means unique
                    non_unique = row[1] if len(row) > 1 else 1
                    key_name = row[2] if len(row) > 2 else None
                    # SHOW INDEX: Non_unique is index 1, Key_name is index 2
                    try:
                        non_unique = row._mapping.get("Non_unique", non_unique)
                        key_name = row._mapping.get("Key_name", key_name)
                    except Exception:
                        pass
                    if key_name and int(non_unique) == 0 and key_name != "PRIMARY":
                        conn.execute(text(f"ALTER TABLE categories DROP INDEX `{key_name}`"))
                conn.execute(text("ALTER TABLE categories MODIFY name VARCHAR(200) NOT NULL"))
        except Exception:
            pass


def _backfill_purchase_item_weights():
    """Copy packaging onto purchase_items from matching stock layers / product."""
    insp = inspect(db.engine)
    try:
        cols = {c["name"] for c in insp.get_columns("purchase_items")}
    except Exception:
        return
    if "unit_weight" not in cols:
        return

    from app.models import PurchaseItem, StockLayer

    items = PurchaseItem.query.filter(
        (PurchaseItem.unit_weight.is_(None)) | (PurchaseItem.unit_weight == 0)
    ).all()
    changed = False
    for it in items:
        layer = (
            StockLayer.query.filter_by(
                product_id=it.product_id,
                source_type="purchase",
                source_id=it.purchase_id,
            )
            .order_by(StockLayer.id.asc())
            .first()
        )
        uw = None
        wu = None
        if layer is not None:
            if layer.unit_weight is not None and Decimal(str(layer.unit_weight)) > 0:
                uw = Decimal(str(layer.unit_weight))
                wu = layer.weight_unit
            elif layer.effective_unit_weight:
                uw = layer.effective_unit_weight
                wu = layer.effective_weight_unit
        if uw is None and it.product and it.product.unit_weight is not None:
            if Decimal(str(it.product.unit_weight or 0)) > 0:
                uw = Decimal(str(it.product.unit_weight))
                wu = it.product.weight_unit
        if uw is None:
            continue
        it.unit_weight = uw
        it.weight_unit = (wu or "kg") if uw else None
        changed = True
    if changed:
        db.session.commit()


def _migrate_fractional_bags_to_open_weight():
    """Convert legacy fractional quantity_remaining into sealed + open_weight_remaining."""
    from decimal import ROUND_DOWN

    from app.models import Product, StockLayer
    from app.services.fifo_service import sync_product_stock
    from app.utils.weight_utils import split_qty_units_and_weight

    insp = inspect(db.engine)
    cols = {c["name"] for c in insp.get_columns("stock_layers")}
    if "open_weight_remaining" not in cols:
        return

    layers = StockLayer.query.all()
    touched_products = set()
    changed = False
    for layer in layers:
        qty = Decimal(str(layer.quantity_remaining or 0))
        open_w = Decimal(str(layer.open_weight_remaining or 0))
        if open_w > 0:
            continue
        if qty <= 0 or qty == qty.to_integral_value(rounding=ROUND_DOWN):
            continue
        uw = None
        if layer.unit_weight is not None and Decimal(str(layer.unit_weight)) > 0:
            uw = Decimal(str(layer.unit_weight))
        elif layer.product and layer.product.unit_weight:
            uw = Decimal(str(layer.product.unit_weight))
        if not uw or uw <= 0:
            continue
        sealed, leftover, _sign = split_qty_units_and_weight(qty, uw)
        layer.quantity_remaining = sealed
        layer.open_weight_remaining = leftover
        touched_products.add(layer.product_id)
        changed = True

    if not changed:
        return
    for pid in touched_products:
        product = db.session.get(Product, pid)
        if product:
            sync_product_stock(product)
    db.session.commit()


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


def _earliest_dashboard_activity_date():
    """First date with sales, returns, purchases, expenses, or inventory loss."""
    from app.models import InventoryLoss
    from app.utils.working_date import get_working_date

    today = get_working_date()
    candidates: list[date] = []

    def _min_date(model, col, *, soft_delete=True):
        try:
            q = db.session.query(func.min(col))
            if soft_delete and hasattr(model, "is_deleted"):
                q = q.filter(model.is_deleted.is_(False))
            raw = q.scalar()
            if raw is None:
                return
            if isinstance(raw, date):
                candidates.append(raw)
            elif hasattr(raw, "date"):
                candidates.append(raw.date())
        except Exception:
            pass

    _min_date(Sale, Sale.sale_date, soft_delete=False)
    _min_date(SaleReturn, SaleReturn.return_date, soft_delete=False)
    _min_date(Purchase, Purchase.purchase_date, soft_delete=False)
    _min_date(Expense, Expense.expense_date)
    _min_date(InventoryLoss, InventoryLoss.loss_date, soft_delete=False)

    if not candidates:
        return today - timedelta(days=29)
    return min(candidates)


def _chart_range_and_granularity(start, end, *, period="all"):
    """Return (start, end, group_fmt, daily) for dashboard chart buckets."""
    from app.utils.working_date import get_working_date

    end = end or get_working_date()
    if not start:
        if period == "all":
            start = _earliest_dashboard_activity_date()
        else:
            start = end - timedelta(days=364)
    span = (end - start).days
    daily = span <= 31
    group_fmt = "%Y-%m-%d" if daily else "%Y-%m"
    return start, end, group_fmt, daily


def _iter_chart_bucket_keys(start: date, end: date, *, daily: bool) -> list[str]:
    """Every day or month in range (inclusive) for aligned chart x-axis."""
    keys: list[str] = []
    if daily:
        d = start
        while d <= end:
            keys.append(d.strftime("%Y-%m-%d"))
            d += timedelta(days=1)
        return keys
    y, m = start.year, start.month
    end_y, end_m = end.year, end.month
    while (y, m) <= (end_y, end_m):
        keys.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return keys


def _format_chart_axis_label(bucket: str, *, daily: bool) -> str:
    """Human-readable x-axis label (e.g. 05 Aug or Aug 2026)."""
    if daily:
        try:
            return date.fromisoformat(bucket).strftime("%d %b")
        except ValueError:
            return bucket
    try:
        parts = bucket.split("-")
        if len(parts) >= 2:
            return date(int(parts[0]), int(parts[1]), 1).strftime("%b %Y")
    except (ValueError, TypeError):
        pass
    return bucket


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


def _pie_payload(items):
    """Chart.js-friendly labels/values plus row metadata for the template."""
    labels = [i["name"] for i in items]
    values = [float(i.get("value") or 0) for i in items]
    return {"labels": labels, "values": values, "rows": items}


def _top_profitable_products(start, end, limit=5):
    """Top products by gross profit (sale line − COGS, net of returns) in period."""
    profit_expr = SaleItem.line_total - SaleItem.cost_of_goods
    sales_q = db.session.query(
        SaleItem.product_id,
        func.coalesce(func.sum(profit_expr), 0),
    ).join(Sale, Sale.id == SaleItem.sale_id)
    for f in _filters(Sale.sale_date, start, end):
        sales_q = sales_q.filter(f)
    sales_map = {r[0]: Decimal(str(r[1] or 0)) for r in sales_q.group_by(SaleItem.product_id).all()}

    ret_expr = SaleReturnItem.line_total - SaleReturnItem.cost_of_goods
    ret_q = db.session.query(
        SaleReturnItem.product_id,
        func.coalesce(func.sum(ret_expr), 0),
    ).join(SaleReturn, SaleReturn.id == SaleReturnItem.sale_return_id)
    for f in _filters(SaleReturn.return_date, start, end):
        ret_q = ret_q.filter(f)
    ret_map = {r[0]: Decimal(str(r[1] or 0)) for r in ret_q.group_by(SaleReturnItem.product_id).all()}

    merged: dict[int, Decimal] = {}
    for pid, amt in sales_map.items():
        merged[pid] = merged.get(pid, Decimal("0")) + amt
    for pid, amt in ret_map.items():
        merged[pid] = merged.get(pid, Decimal("0")) - amt

    ranked = sorted(
        [(pid, profit) for pid, profit in merged.items() if profit > 0],
        key=lambda x: x[1],
        reverse=True,
    )[:limit]
    if not ranked:
        return []

    product_ids = [pid for pid, _ in ranked]
    names = {
        p.id: p.name
        for p in Product.query.filter(Product.id.in_(product_ids)).all()
    }
    items = []
    for pid, profit in ranked:
        items.append(
            {
                "id": pid,
                "name": names.get(pid, f"Product #{pid}"),
                "value": float(profit),
                "detail": f"Profit {float(profit):,.2f}",
            }
        )
    return items


def _top_expense_categories(start, end, limit=5):
    """Top expense categories by total amount in period (with entry count)."""
    q = (
        db.session.query(
            func.coalesce(ExpenseCategory.name, "Uncategorized").label("cat_name"),
            func.coalesce(func.sum(Expense.amount), 0).label("total"),
            func.count(Expense.id).label("cnt"),
        )
        .outerjoin(ExpenseCategory, Expense.category_id == ExpenseCategory.id)
        .filter(Expense.is_deleted.is_(False))
    )
    for f in _filters(Expense.expense_date, start, end):
        q = q.filter(f)
    rows = (
        q.group_by(Expense.category_id, ExpenseCategory.name)
        .order_by(func.sum(Expense.amount).desc())
        .limit(limit)
        .all()
    )
    items = []
    for name, total, cnt in rows:
        items.append(
            {
                "name": name or "Uncategorized",
                "value": float(total or 0),
                "detail": f"{int(cnt or 0)} expense{'s' if int(cnt or 0) != 1 else ''}",
            }
        )
    return items


def get_dashboard_metrics(period="all", start_date=None, end_date=None):
    # Schema ensure runs once at app startup — not on every dashboard hit
    from app.utils.working_date import get_working_date

    start, end = _range_for_filter(period, start_date, end_date)
    today = get_working_date()

    gross_sale = _sum_period(Sale.grand_total, Sale.sale_date, start, end)
    total_returns = _sum_period(SaleReturn.grand_total, SaleReturn.return_date, start, end)
    # Dashboard Total Sale is always after returns
    total_sale = gross_sale - total_returns

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
    gross_cost = cost_q.scalar() or Decimal("0")

    return_cost_q = db.session.query(func.coalesce(func.sum(SaleReturnItem.cost_of_goods), 0)).join(
        SaleReturn, SaleReturn.id == SaleReturnItem.sale_return_id
    )
    for f in _filters(SaleReturn.return_date, start, end):
        return_cost_q = return_cost_q.filter(f)
    return_cost = return_cost_q.scalar() or Decimal("0")
    total_cost = gross_cost - return_cost

    from app.services.inventory_loss_service import period_inventory_loss

    inventory_loss = period_inventory_loss(start, end)

    # Gross / net use sale & COGS after returns; losses & expenses applied after that.
    # Cash in hand = journal Total In + previous − expenses (loans stay journal Out).
    gross_profit = total_sale - total_cost - inventory_loss
    net_profit = gross_profit - total_expense
    if end:
        total_credit, _ = sum_party_balances_as_of("customer", end)
        stock_value = stock_valuation_as_of(end)
    else:
        total_credit = (
            db.session.query(func.coalesce(func.sum(Customer.balance), 0))
            .filter(Customer.is_deleted.is_(False), Customer.balance > 0)
            .scalar()
        ) or Decimal("0")
        stock_value = stock_valuation()
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

    # Chart series — from first activity (period=all) or selected range through today/end
    chart_start, chart_end, group_fmt, chart_daily = _chart_range_and_granularity(
        start, end, period=period
    )

    sales_income = {
        r[0]: float(r[1] or 0)
        for r in db.session.query(
            func.strftime(group_fmt, Sale.sale_date),
            func.coalesce(func.sum(Sale.grand_total), 0),
        )
        .filter(*_filters(Sale.sale_date, chart_start, chart_end))
        .group_by(func.strftime(group_fmt, Sale.sale_date))
        .all()
    }
    returns_income = {
        r[0]: float(r[1] or 0)
        for r in db.session.query(
            func.strftime(group_fmt, SaleReturn.return_date),
            func.coalesce(func.sum(SaleReturn.grand_total), 0),
        )
        .filter(*_filters(SaleReturn.return_date, chart_start, chart_end))
        .group_by(func.strftime(group_fmt, SaleReturn.return_date))
        .all()
    }
    sales_cost = {
        r[0]: float(r[1] or 0)
        for r in db.session.query(
            func.strftime(group_fmt, Sale.sale_date),
            func.coalesce(func.sum(SaleItem.cost_of_goods), 0),
        )
        .join(Sale, Sale.id == SaleItem.sale_id)
        .filter(*_filters(Sale.sale_date, chart_start, chart_end))
        .group_by(func.strftime(group_fmt, Sale.sale_date))
        .all()
    }
    returns_cost = {
        r[0]: float(r[1] or 0)
        for r in db.session.query(
            func.strftime(group_fmt, SaleReturn.return_date),
            func.coalesce(func.sum(SaleReturnItem.cost_of_goods), 0),
        )
        .join(SaleReturn, SaleReturn.id == SaleReturnItem.sale_return_id)
        .filter(*_filters(SaleReturn.return_date, chart_start, chart_end))
        .group_by(func.strftime(group_fmt, SaleReturn.return_date))
        .all()
    }
    expense_map = {
        r[0]: float(r[1] or 0)
        for r in db.session.query(
            func.strftime(group_fmt, Expense.expense_date),
            func.coalesce(func.sum(Expense.amount), 0),
        )
        .filter(Expense.is_deleted.is_(False), *_filters(Expense.expense_date, chart_start, chart_end))
        .group_by(func.strftime(group_fmt, Expense.expense_date))
        .all()
    }
    from app.models import InventoryLoss

    loss_map = {
        r[0]: float(r[1] or 0)
        for r in db.session.query(
            func.strftime(group_fmt, InventoryLoss.loss_date),
            func.coalesce(func.sum(InventoryLoss.amount), 0),
        )
        .filter(*_filters(InventoryLoss.loss_date, chart_start, chart_end))
        .group_by(func.strftime(group_fmt, InventoryLoss.loss_date))
        .all()
    }

    bucket_keys = _iter_chart_bucket_keys(chart_start, chart_end, daily=chart_daily)
    if chart_daily and len(bucket_keys) > 62:
        bucket_keys = bucket_keys[-62:]
    elif period != "all" and not chart_daily and len(bucket_keys) > 24:
        bucket_keys = bucket_keys[-24:]

    chart_labels = []
    chart_income = []
    chart_cost = []
    chart_expense = []
    chart_gross = []
    chart_net = []
    for bucket in bucket_keys:
        income_f = sales_income.get(bucket, 0.0) - returns_income.get(bucket, 0.0)
        cost_f = sales_cost.get(bucket, 0.0) - returns_cost.get(bucket, 0.0)
        exp_f = expense_map.get(bucket, 0.0)
        loss_f = loss_map.get(bucket, 0.0)
        gross_f = income_f - cost_f - loss_f
        net_f = gross_f - exp_f
        chart_labels.append(_format_chart_axis_label(bucket, daily=chart_daily))
        chart_income.append(income_f)
        chart_cost.append(cost_f)
        chart_expense.append(exp_f)
        chart_gross.append(gross_f)
        chart_net.append(net_f)

    first_active = 0
    for i in range(len(bucket_keys)):
        if (
            chart_income[i]
            or chart_cost[i]
            or chart_expense[i]
            or chart_gross[i]
            or chart_net[i]
        ):
            first_active = i
            break
    if first_active > 0:
        chart_labels = chart_labels[first_active:]
        chart_income = chart_income[first_active:]
        chart_cost = chart_cost[first_active:]
        chart_expense = chart_expense[first_active:]
        chart_gross = chart_gross[first_active:]
        chart_net = chart_net[first_active:]

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

    profitable_items = _top_profitable_products(start, end, limit=5)
    expense_categories = _top_expense_categories(start, end, limit=5)

    credit_pie_items = []
    for c in top_credit:
        if c["overdue_days"] > 0:
            detail = f"{c['overdue_days']} days overdue"
        else:
            detail = f"{c['due_days']} / {c['allowed_days']} days"
        credit_pie_items.append(
            {
                "id": c["id"],
                "name": c["name"],
                "value": float(c["balance"] or 0),
                "detail": detail,
            }
        )

    vendor_pie_items = [
        {
            "id": v.id,
            "name": v.name,
            "value": float(v.balance or 0),
            "detail": v.phone or "—",
        }
        for v in top_vendors
    ]

    low_stock_pie_items = []
    for p in low_stock:
        min_s = Decimal(str(p.minimum_stock or 0))
        cur = Decimal(str(p.current_stock or 0))
        gap = min_s - cur if min_s > cur else min_s
        if gap <= 0:
            gap = max(cur, Decimal("0.001"))
        low_stock_pie_items.append(
            {
                "id": p.id,
                "name": p.name,
                "value": float(gap),
                "detail": p.stock_display,
            }
        )

    pies = {
        "profitable_items": _pie_payload(profitable_items),
        "expense_categories": _pie_payload(expense_categories),
        "credit_customers": _pie_payload(credit_pie_items),
        "vendor_payables": _pie_payload(vendor_pie_items),
        "low_stock": _pie_payload(low_stock_pie_items),
    }

    return {
        "total_sale": total_sale,
        "gross_sale": gross_sale,
        "total_returns": total_returns,
        "total_cost": total_cost,
        "inventory_loss": inventory_loss,
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
            "granularity": "day" if chart_daily else "month",
        },
        "pies": pies,
        "recent_expenses": recent_expenses,
        "top_credit": top_credit,
        "top_vendors": top_vendors,
        "low_stock": low_stock,
        "selected_period": period,
        "period_as_of": end,
        "start_date": start,
        "end_date": end,
    }

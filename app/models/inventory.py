from decimal import Decimal

from app.extensions import db
from app.models.mixins import SoftDeleteMixin, TimestampMixin


class UnitType(db.Model):
    __tablename__ = "unit_types"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    description = db.Column(db.String(200), nullable=True)
    is_custom = db.Column(db.Boolean, default=False)


class Unit(db.Model):
    __tablename__ = "units"

    id = db.Column(db.Integer, primary_key=True)
    unit_type_id = db.Column(db.Integer, db.ForeignKey("unit_types.id"), nullable=False)
    name = db.Column(db.String(40), nullable=False)
    symbol = db.Column(db.String(20), nullable=False)
    conversion_factor = db.Column(db.Numeric(14, 6), default=Decimal("1"))

    unit_type = db.relationship("UnitType", backref="units")

    __table_args__ = (db.UniqueConstraint("unit_type_id", "name", name="uq_unit_type_name"),)


class Category(SoftDeleteMixin, TimestampMixin, db.Model):
    __tablename__ = "categories"

    id = db.Column(db.Integer, primary_key=True)
    # Not globally unique — same label allowed as subcategory under another parent.
    # Soft-deleted rows must not block reuse of a name.
    name = db.Column(db.String(200), nullable=False, index=True)
    description = db.Column(db.Text, nullable=True)
    parent_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=True)
    remote_id = db.Column(db.Integer, nullable=True, index=True)

    parent = db.relationship("Category", remote_side=[id], backref="subcategories")


class Product(SoftDeleteMixin, TimestampMixin, db.Model):
    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=False)
    subcategory_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=True)
    name = db.Column(db.String(200), nullable=False, index=True)
    sku = db.Column(db.String(80), unique=True, nullable=False, index=True)
    barcode = db.Column(db.String(80), unique=True, nullable=True, index=True)
    brand = db.Column(db.String(100), nullable=True)
    unit_type_id = db.Column(db.Integer, db.ForeignKey("unit_types.id"), nullable=True)
    purchase_unit_id = db.Column(db.Integer, db.ForeignKey("units.id"), nullable=True)
    sale_unit_id = db.Column(db.Integer, db.ForeignKey("units.id"), nullable=True)
    opening_stock = db.Column(db.Numeric(14, 3), default=Decimal("0"))
    current_stock = db.Column(db.Numeric(18, 6), default=Decimal("0"))
    minimum_stock = db.Column(db.Numeric(14, 3), default=Decimal("0"))
    purchase_price = db.Column(db.Numeric(14, 2), default=Decimal("0"))
    sale_price = db.Column(db.Numeric(14, 2), default=Decimal("0"))
    wholesale_price = db.Column(db.Numeric(14, 2), default=Decimal("0"))
    retail_price = db.Column(db.Numeric(14, 2), default=Decimal("0"))
    # One packaging unit's weight/volume (e.g. bag = 50 kg). Used for partial-weight sales.
    unit_weight = db.Column(db.Numeric(14, 3), nullable=True)
    weight_unit = db.Column(db.String(10), nullable=True)  # kg | g | L | ml
    tax_rate = db.Column(db.Numeric(5, 2), default=Decimal("0"))
    description = db.Column(db.Text, nullable=True)
    photo = db.Column(db.String(255), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    remote_id = db.Column(db.Integer, nullable=True, index=True)

    category = db.relationship("Category", foreign_keys=[category_id])
    subcategory = db.relationship("Category", foreign_keys=[subcategory_id])
    unit_type = db.relationship("UnitType")
    purchase_unit = db.relationship("Unit", foreign_keys=[purchase_unit_id])
    sale_unit = db.relationship("Unit", foreign_keys=[sale_unit_id])

    @property
    def is_low_stock(self):
        return self.current_stock <= self.minimum_stock

    @property
    def active_label(self):
        return "Active" if self.is_active else "Inactive"

    @property
    def photo_url(self):
        if not self.photo:
            return None
        return f"/static/uploads/{self.photo}"

    @property
    def has_unit_weight(self):
        try:
            return Decimal(str(self.unit_weight or 0)) > 0
        except Exception:
            return False

    @property
    def stock_display(self):
        from app.utils.weight_utils import format_stock_display

        return format_stock_display(self)

    @property
    def stock_total_display(self):
        """List/summary: sealed bags + open kg (e.g. 304 + 19 kg)."""
        from app.utils.weight_utils import format_stock_total_display

        return format_stock_total_display(self)

    def qty_display(self, qty) -> str:
        from app.utils.weight_utils import format_qty_display

        return format_qty_display(qty, self.unit_weight, self.weight_unit or "kg")


class StockLayer(db.Model):
    """FIFO stock batches — each purchase creates its own cost & sale price."""

    __tablename__ = "stock_layers"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)
    quantity_received = db.Column(db.Numeric(14, 3), nullable=False, default=Decimal("0"))
    quantity_remaining = db.Column(db.Numeric(14, 3), nullable=False)
    unit_cost = db.Column(db.Numeric(14, 2), nullable=False)  # purchase / cost price
    sale_price = db.Column(db.Numeric(14, 2), nullable=True)  # sell price for this batch
    # Packaging weight for this batch only (e.g. 20 kg bags vs 100 kg bags).
    unit_weight = db.Column(db.Numeric(14, 3), nullable=True)
    weight_unit = db.Column(db.String(10), nullable=True)  # kg | g | L | ml
    # Kg/L left from bags opened for partial (open) sales. Sealed bags stay in quantity_remaining.
    open_weight_remaining = db.Column(db.Numeric(14, 3), nullable=True, default=Decimal("0"))
    source_type = db.Column(db.String(30), nullable=False)
    source_id = db.Column(db.Integer, nullable=True)
    batch_number = db.Column(db.String(80), nullable=True, index=True)
    expiry_date = db.Column(db.Date, nullable=True, index=True)
    vendor_id = db.Column(db.Integer, db.ForeignKey("vendors.id"), nullable=True, index=True)
    invoice_no = db.Column(db.String(80), nullable=True, index=True)
    received_at = db.Column(db.DateTime, nullable=False, index=True)
    notes = db.Column(db.Text, nullable=True)

    product = db.relationship("Product", backref="stock_layers")
    vendor = db.relationship("Vendor")

    @property
    def effective_unit_weight(self):
        """Product weight (one per product); fall back to batch only if product unset."""
        product = self.product
        if product and product.unit_weight is not None and Decimal(str(product.unit_weight)) > 0:
            return Decimal(str(product.unit_weight))
        if self.unit_weight is not None and Decimal(str(self.unit_weight)) > 0:
            return Decimal(str(self.unit_weight))
        return None

    @property
    def effective_weight_unit(self):
        product = self.product
        if product and product.weight_unit:
            return product.weight_unit
        if self.weight_unit:
            return self.weight_unit
        return "kg"

    @property
    def open_weight(self):
        return Decimal(str(self.open_weight_remaining or 0))

    @property
    def stock_qty_equivalent(self):
        """Sealed bags + open weight converted to bag units (for product.current_stock)."""
        sealed = Decimal(str(self.quantity_remaining or 0))
        open_w = self.open_weight
        uw = self.effective_unit_weight
        if uw and uw > 0 and open_w > 0:
            return sealed + (open_w / uw)
        return sealed

    def has_stock(self) -> bool:
        return Decimal(str(self.quantity_remaining or 0)) > 0 or self.open_weight > 0

    @property
    def quantity_purchased(self):
        """Original bought qty for this batch (falls back to remaining for old rows)."""
        recv = self.quantity_received
        if recv is None:
            return self.quantity_remaining or Decimal("0")
        return Decimal(str(recv))

    @property
    def quantity_used(self):
        bought = self.quantity_purchased
        left = self.stock_qty_equivalent
        used = bought - left
        return used if used > 0 else Decimal("0")


class StockMovement(db.Model):
    __tablename__ = "stock_movements"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)
    movement_type = db.Column(db.String(30), nullable=False)
    quantity = db.Column(db.Numeric(18, 6), nullable=False)
    unit_cost = db.Column(db.Numeric(14, 2), nullable=True)
    balance_after = db.Column(db.Numeric(18, 6), nullable=False)
    reference_type = db.Column(db.String(30), nullable=True)
    reference_id = db.Column(db.Integer, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, nullable=False, index=True)

    product = db.relationship("Product", backref="movements")
    created_by = db.relationship("User")


class InventoryAdjustment(db.Model):
    __tablename__ = "inventory_adjustments"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    adjustment_type = db.Column(db.String(30), nullable=False)
    quantity = db.Column(db.Numeric(14, 3), nullable=False)
    notes = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, nullable=False)

    product = db.relationship("Product")
    created_by = db.relationship("User")


class InventoryLoss(db.Model):
    """
    Non-cash inventory loss (shrinkage / shortage).

    Does NOT affect cash in hand — only gross/net profit on the dashboard.
    """

    __tablename__ = "inventory_losses"

    id = db.Column(db.Integer, primary_key=True)
    loss_date = db.Column(db.Date, nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)
    # adjustment_shortage | backorder_trueup (optional future)
    source_type = db.Column(db.String(40), nullable=False, index=True)
    source_id = db.Column(db.Integer, nullable=True)
    quantity = db.Column(db.Numeric(18, 6), nullable=False, default=Decimal("0"))
    unit_cost = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0"))
    amount = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0"))
    notes = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, nullable=False)

    product = db.relationship("Product")
    created_by = db.relationship("User")


class SaleItemBackorder(db.Model):
    """
    Qty/weight sold when stock was insufficient (negative stock).

    Later purchases fulfill these FIFO and true-up SaleItem.cost_of_goods.
    """

    __tablename__ = "sale_item_backorders"

    id = db.Column(db.Integer, primary_key=True)
    sale_item_id = db.Column(db.Integer, db.ForeignKey("sale_items.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)
    qty_backordered = db.Column(db.Numeric(18, 6), nullable=False, default=Decimal("0"))
    qty_fulfilled = db.Column(db.Numeric(18, 6), nullable=False, default=Decimal("0"))
    weight_backordered = db.Column(db.Numeric(14, 3), nullable=True)
    weight_fulfilled = db.Column(db.Numeric(14, 3), nullable=True)
    # open | partial | closed
    status = db.Column(db.String(20), nullable=False, default="open", index=True)
    created_at = db.Column(db.DateTime, nullable=False)
    updated_at = db.Column(db.DateTime, nullable=True)

    sale_item = db.relationship("SaleItem", backref=db.backref("backorder", uselist=False))
    product = db.relationship("Product")

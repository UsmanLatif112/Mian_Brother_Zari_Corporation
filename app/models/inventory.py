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
    name = db.Column(db.String(100), nullable=False, unique=True)
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
    current_stock = db.Column(db.Numeric(14, 3), default=Decimal("0"))
    minimum_stock = db.Column(db.Numeric(14, 3), default=Decimal("0"))
    purchase_price = db.Column(db.Numeric(14, 2), default=Decimal("0"))
    sale_price = db.Column(db.Numeric(14, 2), default=Decimal("0"))
    wholesale_price = db.Column(db.Numeric(14, 2), default=Decimal("0"))
    retail_price = db.Column(db.Numeric(14, 2), default=Decimal("0"))
    tax_rate = db.Column(db.Numeric(5, 2), default=Decimal("0"))
    description = db.Column(db.Text, nullable=True)
    photo = db.Column(db.String(255), nullable=True)
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
    def photo_url(self):
        if not self.photo:
            return None
        return f"/static/uploads/{self.photo}"


class StockLayer(db.Model):
    """FIFO stock batches — each purchase creates its own cost & sale price."""

    __tablename__ = "stock_layers"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)
    quantity_received = db.Column(db.Numeric(14, 3), nullable=False, default=Decimal("0"))
    quantity_remaining = db.Column(db.Numeric(14, 3), nullable=False)
    unit_cost = db.Column(db.Numeric(14, 2), nullable=False)  # purchase / cost price
    sale_price = db.Column(db.Numeric(14, 2), nullable=True)  # sell price for this batch
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
    def quantity_purchased(self):
        """Original bought qty for this batch (falls back to remaining for old rows)."""
        recv = self.quantity_received
        if recv is None:
            return self.quantity_remaining or Decimal("0")
        return Decimal(str(recv))

    @property
    def quantity_used(self):
        bought = self.quantity_purchased
        left = Decimal(str(self.quantity_remaining or 0))
        used = bought - left
        return used if used > 0 else Decimal("0")


class StockMovement(db.Model):
    __tablename__ = "stock_movements"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)
    movement_type = db.Column(db.String(30), nullable=False)
    quantity = db.Column(db.Numeric(14, 3), nullable=False)
    unit_cost = db.Column(db.Numeric(14, 2), nullable=True)
    balance_after = db.Column(db.Numeric(14, 3), nullable=False)
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

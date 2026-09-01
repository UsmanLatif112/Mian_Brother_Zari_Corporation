"""Explore SONA DAP stock: layers vs current_stock vs movement net."""
import sqlite3
from decimal import Decimal
from pathlib import Path

db = Path(__file__).resolve().parents[1] / "instance" / "mbzc_erp.db"
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row
cur = con.cursor()

# Find SONA DAP
prod = cur.execute(
    "SELECT id, name, sku, current_stock, unit_weight, weight_unit, sale_price FROM products WHERE name LIKE '%SONA DAP%'"
).fetchone()
if not prod:
    print("SONA DAP not found")
    raise SystemExit(1)

pid = prod["id"]
print("=== PRODUCT ===")
print(dict(prod))

print("\n=== STOCK LAYERS ===")
layer_sum = Decimal("0")
for r in cur.execute(
    "SELECT id, quantity_remaining, open_weight_remaining, unit_cost, sale_price, received_at, source_type, source_id FROM stock_layers WHERE product_id=? ORDER BY id",
    (pid,),
):
    d = dict(r)
    sealed = Decimal(str(d["quantity_remaining"] or 0))
    open_w = Decimal(str(d["open_weight_remaining"] or 0))
    uw = Decimal("50")  # product unit_weight
    equiv = sealed + (open_w / uw if uw else Decimal("0"))
    layer_sum += equiv
    print(d, "sealed=", float(sealed), "open_kg=", float(open_w), "equiv=", float(equiv))
print("Layer sum (sealed + open/50kg):", float(layer_sum))

print("\n=== OPEN BACKORDERS ===")
bo_sum = Decimal("0")
for r in cur.execute(
    """
    SELECT b.id, b.qty_backordered, b.qty_fulfilled, s.invoice_no
    FROM sale_item_backorders b
    JOIN sale_items si ON si.id = b.sale_item_id
    JOIN sales s ON s.id = si.sale_id
    WHERE si.product_id=?
    """,
    (pid,),
):
    d = dict(r)
    open_bo = Decimal(str(d["qty_backordered"] or 0)) - Decimal(str(d["qty_fulfilled"] or 0))
    bo_sum += max(open_bo, Decimal("0"))
    print(d, "open=", float(open_bo))
print("Open backorder total:", float(bo_sum))

print("\n=== MOVEMENT NET BY TYPE ===")
for r in cur.execute(
    """
    SELECT movement_type, reference_type, COUNT(*) as cnt, SUM(quantity) as total_qty
    FROM stock_movements WHERE product_id=?
    GROUP BY movement_type, reference_type ORDER BY movement_type
    """,
    (pid,),
):
    print(dict(r))

print("\n=== MOVEMENT RUNNING NET (all movements) ===")
running = Decimal("0")
for r in cur.execute(
    """
    SELECT id, movement_type, quantity, reference_type, reference_id, notes, created_at, balance_after
    FROM stock_movements WHERE product_id=? ORDER BY id
    """,
    (pid,),
):
    q = Decimal(str(r["quantity"] or 0))
    running += q
    print(
        r["id"],
        r["created_at"][:19] if r["created_at"] else "",
        r["movement_type"],
        float(q),
        "run=",
        float(running),
        "bal_after=",
        r["balance_after"],
        (r["notes"] or "")[:50],
    )
print("Final movement net:", float(running))
print("Last balance_after:", r["balance_after"])
print("current_stock:", prod["current_stock"])
print("layer_sum - backorder:", float(layer_sum - bo_sum))

print("\n=== VOID MOVEMENTS FOR SONA DAP ===")
void_in = Decimal("0")
void_out_related = Decimal("0")
for r in cur.execute(
    """
    SELECT id, quantity, notes, reference_id, created_at
    FROM stock_movements WHERE product_id=? AND notes LIKE 'Void sale%'
    ORDER BY id
    """,
    (pid,),
):
    q = Decimal(str(r["quantity"] or 0))
    void_in += q
    print(dict(r))
print("Total void restore qty:", float(void_in))

print("\n=== SALE OUT for SONA DAP ===")
sale_out = Decimal("0")
for r in cur.execute(
    """
    SELECT id, quantity, reference_id, notes, created_at, balance_after
    FROM stock_movements WHERE product_id=? AND movement_type='sale_out'
    ORDER BY id
    """,
    (pid,),
):
    q = Decimal(str(r["quantity"] or 0))
    sale_out += q
    print(dict(r))
print("Total sale_out (negative):", float(sale_out))

print("\n=== ACTIVE SALE LINES (current sales table) ===")
active_sold = Decimal("0")
for r in cur.execute(
    """
    SELECT s.id, s.invoice_no, si.quantity, si.cost_of_goods
    FROM sale_items si JOIN sales s ON s.id=si.sale_id
    WHERE si.product_id=? ORDER BY s.id
    """,
    (pid,),
):
    q = Decimal(str(r["quantity"] or 0))
    active_sold += q
    print(dict(r))
print("Active sales qty total:", float(active_sold))

print("\n=== PURCHASE IN ===")
purchase_in = Decimal("0")
for r in cur.execute(
    """
    SELECT id, quantity, unit_cost, notes, created_at
    FROM stock_movements WHERE product_id=? AND movement_type='purchase_in'
    ORDER BY id
    """,
    (pid,),
):
    q = Decimal(str(r["quantity"] or 0))
    purchase_in += q
    print(dict(r))
print("Total purchase_in:", float(purchase_in))

print("\n=== DUPLICATE VOID CHECK (same invoice voided multiple times) ===")
for r in cur.execute(
    """
    SELECT notes, COUNT(*) as cnt, SUM(quantity) as total
    FROM stock_movements
    WHERE product_id=? AND notes LIKE 'Void sale%'
    GROUP BY notes HAVING cnt > 1
    """,
    (pid,),
):
    print(dict(r))

con.close()

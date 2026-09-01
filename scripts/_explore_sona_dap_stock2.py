"""Deep dive SONA DAP: all layers, batch edits, expected vs actual stock."""
import sqlite3
from decimal import Decimal
from pathlib import Path

db = Path(__file__).resolve().parents[1] / "instance" / "mbzc_erp.db"
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row
cur = con.cursor()
pid = 1

print("=== ALL LAYERS (including zero) ===")
for r in cur.execute(
    """
    SELECT id, quantity_received, quantity_remaining, open_weight_remaining,
           unit_cost, source_type, source_id, received_at, notes
    FROM stock_layers WHERE product_id=? ORDER BY id
    """,
    (pid,),
):
    d = dict(r)
    sealed = Decimal(str(d["quantity_remaining"] or 0))
    open_w = Decimal(str(d["open_weight_remaining"] or 0))
    equiv = sealed + open_w / Decimal("50")
    print(f"  L{d['id']}: recv={d['quantity_received']} rem={sealed} open={open_w}kg equiv={float(equiv):.3f} src={d['source_type']}:{d['source_id']} @ {d['received_at'][:10]}")

print("\n=== BATCH EDIT MOVEMENTS ===")
for r in cur.execute(
    """
    SELECT id, movement_type, quantity, notes, created_at, balance_after
    FROM stock_movements WHERE product_id=? AND movement_type LIKE 'adjust%'
    ORDER BY id
    """,
    (pid,),
):
    print(dict(r))

print("\n=== EXPECTED STOCK CALC ===")
# Method A: purchase + adjustments - active sales
purchase = Decimal("33.26")
adj = Decimal("0")
for r in cur.execute(
    "SELECT quantity FROM stock_movements WHERE product_id=? AND movement_type LIKE 'adjust%'",
    (pid,),
):
    adj += Decimal(str(r["quantity"] or 0))
active = Decimal("0")
for r in cur.execute(
    "SELECT quantity FROM sale_items si JOIN sales s ON s.id=si.sale_id WHERE si.product_id=?",
    (pid,),
):
    active += Decimal(str(r["quantity"] or 0))
print(f"Purchase {purchase} + adjustments {adj} - active sales {active} = {purchase + adj - active}")

# Method B: sum non-zero layers
layer_total = Decimal("0")
for r in cur.execute(
    "SELECT quantity_remaining, open_weight_remaining FROM stock_layers WHERE product_id=?",
    (pid,),
):
    layer_total += Decimal(str(r["quantity_remaining"] or 0))
    layer_total += Decimal(str(r["open_weight_remaining"] or 0)) / Decimal("50")

prod = cur.execute("SELECT current_stock FROM products WHERE id=?", (pid,)).fetchone()
print(f"Layer sum equiv: {layer_total}")
print(f"products.current_stock: {prod['current_stock']}")

print("\n=== INV-000038 full cycle (problem case with open weight) ===")
for r in cur.execute(
    """
    SELECT id, movement_type, quantity, notes, reference_id, created_at, balance_after
    FROM stock_movements WHERE product_id=? AND (notes LIKE '%038%' OR reference_id=38)
    ORDER BY id
    """,
    (pid,),
):
    print(dict(r))

print("\n=== VOID layers still holding stock ===")
for r in cur.execute(
    """
    SELECT id, quantity_remaining, open_weight_remaining, source_id, notes
    FROM stock_layers WHERE product_id=? AND source_type='sale_return'
      AND (quantity_remaining > 0 OR open_weight_remaining > 0)
    ORDER BY id
    """,
    (pid,),
):
    print(dict(r))

print("\n=== Original purchase layer ===")
for r in cur.execute(
    "SELECT * FROM stock_layers WHERE product_id=? AND source_type='purchase'",
    (pid,),
):
    print(dict(r))

print("\n=== If void layers removed, stock would be ===")
non_void = Decimal("0")
for r in cur.execute(
    """
    SELECT quantity_remaining, open_weight_remaining, source_type
    FROM stock_layers WHERE product_id=?
    """,
    (pid,),
):
    if r["source_type"] == "sale_return":
        continue
    non_void += Decimal(str(r["quantity_remaining"] or 0))
    non_void += Decimal(str(r["open_weight_remaining"] or 0)) / Decimal("50")
print(f"Non-void layers only: {non_void}")

con.close()

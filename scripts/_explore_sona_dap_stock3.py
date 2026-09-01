"""Check if re-sales after void consumed void layers (FIFO allocation trace)."""
import sqlite3
from pathlib import Path

db = Path(__file__).resolve().parents[1] / "instance" / "mbzc_erp.db"
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row
cur = con.cursor()
pid = 1

print("=== Sale items for SONA DAP with COGS ===")
for r in cur.execute(
    """
    SELECT s.id, s.invoice_no, si.quantity, si.cost_of_goods,
           ROUND(si.cost_of_goods / NULLIF(si.quantity,0), 2) as unit_cogs
    FROM sale_items si JOIN sales s ON s.id=si.sale_id
    WHERE si.product_id=? ORDER BY s.id
    """,
    (pid,),
):
    print(dict(r))

print("\n=== Void layer costs vs active sale costs ===")
print("Void layers:")
for r in cur.execute(
    "SELECT id, quantity_remaining, unit_cost, source_id FROM stock_layers WHERE product_id=? AND source_type='sale_return'",
    (pid,),
):
    print(dict(r))

print("\n=== INV-000047 sale ids 47 vs 69 ===")
for r in cur.execute("SELECT id, invoice_no FROM sales WHERE invoice_no='INV-000047'"):
    print(dict(r))

print("\n=== Movements 263-275 timeline (047 edit) ===")
for r in cur.execute(
    "SELECT id, movement_type, quantity, notes, created_at, balance_after FROM stock_movements WHERE id BETWEEN 260 AND 276 AND product_id=1"
):
    print(dict(r))

print("\n=== All products with void orphan layers (sale_return with stock) ===")
for r in cur.execute(
    """
    SELECT p.name, COUNT(*) as void_layers,
           SUM(l.quantity_remaining + COALESCE(l.open_weight_remaining,0)/NULLIF(p.unit_weight,0)) as equiv
    FROM stock_layers l JOIN products p ON p.id=l.product_id
    WHERE l.source_type='sale_return'
      AND (l.quantity_remaining > 0 OR COALESCE(l.open_weight_remaining,0) > 0)
    GROUP BY p.id ORDER BY equiv DESC LIMIT 10
    """
):
    print(dict(r))

print("\n=== Stock mismatch all products (layer sum vs current_stock) ===")
for r in cur.execute(
    """
    SELECT p.id, p.name, p.current_stock,
           (SELECT SUM(l.quantity_remaining + COALESCE(l.open_weight_remaining,0)/NULLIF(p.unit_weight,1))
            FROM stock_layers l WHERE l.product_id=p.id) as layer_equiv
    FROM products p WHERE p.is_active=1
    HAVING ABS(COALESCE(layer_equiv,0) - COALESCE(current_stock,0)) > 0.01
    ORDER BY ABS(COALESCE(layer_equiv,0) - COALESCE(current_stock,0)) DESC
    LIMIT 15
    """
):
    d = dict(r)
    d["diff"] = float(d["layer_equiv"] or 0) - float(d["current_stock"] or 0)
    print(d)

con.close()

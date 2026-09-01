import sqlite3
from pathlib import Path

db = Path(__file__).resolve().parents[1] / "instance" / "mbzc_erp.db"
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row
cur = con.cursor()

print("=== Duplicate void movements per invoice ===")
for r in cur.execute(
    """
    SELECT notes, COUNT(*) as cnt, SUM(quantity) as total_qty
    FROM stock_movements
    WHERE notes LIKE 'Void sale%'
    GROUP BY notes, product_id, reference_id
    HAVING cnt > 1
    ORDER BY cnt DESC
    LIMIT 15
    """
):
    print(dict(r))

print("\n=== INV-000052 timeline ===")
for r in cur.execute(
    """
    SELECT id, movement_type, quantity, notes, created_at, reference_id
    FROM stock_movements WHERE notes LIKE '%INV-000052%' OR reference_id=52
    ORDER BY id
    """
):
    print(dict(r))

print("\n=== Sales with invoice INV-000052 ===")
for r in cur.execute("SELECT id, invoice_no FROM sales WHERE invoice_no='INV-000052'"):
    print(dict(r))

print("\n=== Edit pattern: delete+update pairs for Aug 26 ===")
for r in cur.execute(
    """
    SELECT action, entity_type, entity_id, details, created_at
    FROM audit_logs
    WHERE entity_type='sale' AND action IN ('delete','update')
      AND created_at >= '2026-08-26'
    ORDER BY id
    LIMIT 30
    """
):
    d = dict(r)
    inv = d['details'][:20] if isinstance(d['details'], str) else ''
    print(d['action'], d['entity_id'], inv, d['created_at'])

con.close()

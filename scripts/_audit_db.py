"""Quick schema + row-count audit for a SQLite mbzc_erp.db."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEY_TABLES = (
    "customers",
    "vendors",
    "products",
    "sales",
    "users",
    "stock_layers",
    "stock_movements",
    "sale_returns",
    "sale_return_items",
    "inventory_losses",
    "sale_item_backorders",
    "salesmen",
    "customer_photos",
    "vendor_photos",
    "account_cash_setups",
    "account_amount_taken",
)
KEY_COLS = {
    "customers": ["is_active", "customer_type", "photo", "cnic"],
    "vendors": ["is_active", "photo", "cnic"],
    "products": ["is_active", "unit_weight", "weight_unit", "photo"],
    "sales": ["salesman_id"],
    "stock_layers": ["open_weight_remaining", "quantity_received", "sale_price"],
}


def audit(db_path: Path) -> None:
    print(f"\n=== {db_path} ===")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = {r[0] for r in cur.fetchall()}
    print(f"Tables: {len(tables)}")
    for t in KEY_TABLES:
        if t in tables:
            cur.execute(f"SELECT COUNT(*) FROM {t}")
            print(f"  {t}: {cur.fetchone()[0]} rows")
        else:
            print(f"  {t}: MISSING TABLE")
    for table, cols in KEY_COLS.items():
        if table not in tables:
            continue
        cur.execute(f"PRAGMA table_info({table})")
        have = {r[1] for r in cur.fetchall()}
        missing = [c for c in cols if c not in have]
        if missing:
            print(f"  {table} missing cols: {missing}")
        else:
            print(f"  {table} key cols: OK")
    conn.close()


if __name__ == "__main__":
    paths = sys.argv[1:] or [
        str(ROOT / "releases" / "database" / "instance" / "mbzc_erp.db"),
        str(ROOT / "instance" / "mbzc_erp.db"),
    ]
    for p in paths:
        path = Path(p)
        if path.is_file():
            audit(path)

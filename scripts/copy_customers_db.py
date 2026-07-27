"""Inspect and copy customers from mbzc_erp.db -> mbzc_erp_2.db."""
from __future__ import annotations

import argparse
import shutil
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / "databses"
SRC = BASE / "mbzc_erp.db"
DST = BASE / "mbzc_erp_2.db"


def inspect(path: Path) -> None:
    print(f"=== {path.name} ({path.stat().st_size} bytes) ===")
    con = sqlite3.connect(path)
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY 1")]
    print("tables:", tables)
    if "customers" in tables:
        cols = [r[1] for r in con.execute("PRAGMA table_info(customers)")]
        n = con.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        print("customers cols:", cols)
        print("customers count:", n)
        print("sample:", con.execute("SELECT id, name, phone, balance FROM customers LIMIT 3").fetchall())
    if "ledger_entries" in tables:
        n = con.execute("SELECT COUNT(*) FROM ledger_entries WHERE party_type='customer'").fetchone()[0]
        print("customer ledger rows:", n)
    if "customer_receivings" in tables:
        n = con.execute("SELECT COUNT(*) FROM customer_receivings").fetchone()[0]
        print("customer_receivings:", n)
    con.close()


def copy_customers(*, replace: bool) -> None:
    if not SRC.is_file() or not DST.is_file():
        raise SystemExit(f"Missing DB files in {BASE}")

    # Backup destination first
    bak = DST.with_suffix(".db.bak_before_customer_copy")
    shutil.copy2(DST, bak)
    print(f"Backup: {bak}")

    src = sqlite3.connect(SRC)
    dst = sqlite3.connect(DST)
    src.row_factory = sqlite3.Row

    src_cols = [r[1] for r in src.execute("PRAGMA table_info(customers)")]
    dst_cols = [r[1] for r in dst.execute("PRAGMA table_info(customers)")]
    common = [c for c in src_cols if c in dst_cols]
    if "id" not in common:
        raise SystemExit("customers.id missing")

    print("Copying columns:", common)
    before = dst.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    if replace:
        dst.execute("DELETE FROM customers")
        print(f"Cleared destination customers ({before} rows)")

    rows = src.execute(f"SELECT {', '.join(common)} FROM customers").fetchall()
    placeholders = ", ".join("?" for _ in common)
    col_list = ", ".join(common)
    inserted = 0
    updated = 0
    skipped = 0

    for row in rows:
        values = [row[c] for c in common]
        existing = dst.execute("SELECT id FROM customers WHERE id=?", (row["id"],)).fetchone()
        if existing:
            if replace:
                # already deleted all; shouldn't hit
                pass
            else:
                # update in place by id
                set_clause = ", ".join(f"{c}=?" for c in common if c != "id")
                upd_vals = [row[c] for c in common if c != "id"] + [row["id"]]
                dst.execute(f"UPDATE customers SET {set_clause} WHERE id=?", upd_vals)
                updated += 1
                continue
        try:
            dst.execute(f"INSERT INTO customers ({col_list}) VALUES ({placeholders})", values)
            inserted += 1
        except sqlite3.IntegrityError:
            # name/phone unique? try update by name
            skipped += 1

    dst.commit()
    after = dst.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    print(f"Done. inserted={inserted} updated={updated} skipped={skipped} dest_count={after}")
    src.close()
    dst.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--copy", action="store_true")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Delete all customers in destination before copy",
    )
    args = parser.parse_args()
    if args.inspect or not args.copy:
        inspect(SRC)
        inspect(DST)
    if args.copy:
        copy_customers(replace=args.replace)


if __name__ == "__main__":
    main()

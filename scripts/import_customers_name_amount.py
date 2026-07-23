"""
Import customers from customer_data.csv into the database.

- customer name -> Customer.name
- amount       -> opening_balance and balance (+ opening ledger entry)
- other fields left blank / defaults (editable later in the UI)
"""

from __future__ import annotations

import csv
import os
import sys
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv

load_dotenv()

from app import create_app
from app.extensions import db
from app.models import Customer
from app.services.ledger_service import post_ledger_entry


CSV_PATH = ROOT / "customer_data.csv"


def parse_amount(raw) -> Decimal:
    text = str(raw or "").strip().replace(",", "")
    if not text:
        return Decimal("0")
    try:
        return Decimal(text)
    except InvalidOperation:
        raise ValueError(f"Invalid amount: {raw!r}")


def main() -> int:
    if not CSV_PATH.is_file():
        print(f"CSV not found: {CSV_PATH}")
        return 1

    app = create_app()
    created = 0
    skipped = 0
    errors = []

    with app.app_context():
        with CSV_PATH.open(encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            fieldmap = {((k or "").strip().lower()): k for k in (reader.fieldnames or [])}
            name_key = fieldmap.get("customer name") or fieldmap.get("name")
            amount_key = fieldmap.get("amount") or fieldmap.get("opening balance")
            if not name_key or not amount_key:
                print("CSV must have columns: customer name, amount")
                print("Found:", reader.fieldnames)
                return 1

            for i, row in enumerate(reader, start=2):
                name = (row.get(name_key) or "").strip()
                if not name:
                    skipped += 1
                    continue
                try:
                    amount = parse_amount(row.get(amount_key))
                except ValueError as exc:
                    errors.append(f"line {i}: {exc}")
                    continue

                existing = (
                    Customer.query.filter(
                        Customer.is_deleted.is_(False),
                        Customer.name.ilike(name),
                    ).first()
                )
                if existing:
                    skipped += 1
                    continue

                customer = Customer(
                    name=name,
                    phone=None,
                    cnic=None,
                    address=None,
                    old_book_no=None,
                    customer_type="good",
                    joined_date=date.today(),
                    opening_balance=amount,
                    credit_limit=Decimal("0"),
                    balance=amount,
                    notes=None,
                )
                db.session.add(customer)
                db.session.flush()

                if amount != 0:
                    debit = amount if amount > 0 else Decimal("0")
                    credit = abs(amount) if amount < 0 else Decimal("0")
                    post_ledger_entry(
                        "customer",
                        customer.id,
                        "opening",
                        debit=debit,
                        credit=credit,
                        entry_date=date.today(),
                        notes="Opening balance (imported)",
                    )
                created += 1

        db.session.commit()

        total = Customer.query.filter(Customer.is_deleted.is_(False)).count()
        print("Import finished")
        print(f"  created : {created}")
        print(f"  skipped : {skipped} (blank name or already exists)")
        print(f"  errors  : {len(errors)}")
        for e in errors[:20]:
            print(f"    - {e}")
        if len(errors) > 20:
            print(f"    ... and {len(errors) - 20} more")
        print(f"  customers in DB now: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

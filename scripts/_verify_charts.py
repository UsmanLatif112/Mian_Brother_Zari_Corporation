"""Smoke-test every page that should render a Chart.js graph."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from app.models import Customer, Product, Salesman, User, Vendor


def main() -> int:
    app = create_app()
    with app.app_context():
        cust = Customer.query.filter_by(is_deleted=False).first()
        vend = Vendor.query.filter_by(is_deleted=False).first()
        prod = Product.query.filter_by(is_deleted=False).first()
        sm = Salesman.query.first()
        user = User.query.first()
        if not user:
            print("No user in DB — cannot test authenticated pages.")
            return 1

        checks: list[tuple[str, list[str]]] = [
            ("/", ["profitChart", "dashboard.js", "dash-pie-canvas"]),
            ("/sales/", ["salesPerformanceChart", "sales-page-chart.js"]),
            ("/purchases/", ["purchasesPerformanceChart", "purchases-page-chart.js"]),
            ("/account/", ["accountPerformanceChart", "account-page-chart.js"]),
            ("/journal/", ["journalPerformanceChart", "journal-detail.js"]),
            ("/expenses/", ["partyAccountPie", "party-detail.js"]),
            ("/vendors/", ["vendorListChart", "list-page-charts.js"]),
            ("/customers/", ["customerListChart", "list-page-charts.js"]),
            ("/inventory/", ["inventoryListChart", "list-page-charts.js"]),
            ("/salesmen/", ["salesmanListChart", "list-page-charts.js"]),
        ]
        if cust:
            checks.append((f"/customers/{cust.id}", ["partyAccountPie", "party-detail.js"]))
        if vend:
            checks.append((f"/vendors/{vend.id}", ["partyAccountPie", "party-detail.js"]))
        if prod:
            checks.append(
                (f"/inventory/products/{prod.id}", ["productPerformanceChart", "product-detail.js"])
            )
        if sm:
            checks.append((f"/salesmen/{sm.id}", ["salesmanPerformanceChart", "salesman-detail.js"]))

        js_files = [
            "sales-page-chart.js",
            "purchases-page-chart.js",
            "account-page-chart.js",
            "list-page-charts.js",
            "dashboard.js",
            "journal-detail.js",
            "party-detail.js",
            "product-detail.js",
            "salesman-detail.js",
        ]

        failed = []
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["_user_id"] = str(user.id)
                sess["_fresh"] = True

            for path, needles in checks:
                resp = client.get(path)
                html = resp.get_data(as_text=True)
                if resp.status_code != 200:
                    failed.append(f"{path}: HTTP {resp.status_code}")
                    continue
                missing = [n for n in needles if n not in html]
                if missing:
                    failed.append(f"{path}: missing {missing}")

            for js in js_files:
                resp = client.get(f"/static/js/{js}")
                if resp.status_code != 200 or len(resp.data) < 500:
                    failed.append(f"/static/js/{js}: empty or missing ({len(resp.data)} bytes)")

        if failed:
            print("Chart verification FAILED:")
            for line in failed:
                print(" -", line)
            return 1

        print(f"Chart verification OK — {len(checks)} pages, {len(js_files)} JS files.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

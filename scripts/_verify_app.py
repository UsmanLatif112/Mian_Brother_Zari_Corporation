"""Full-app smoke test: pages, modals, static assets, key APIs."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from app.models import Customer, Product, Salesman, User, Vendor
from app.models.user import UserRole


PAGE_CHECKS: list[tuple[str, list[str], UserRole | None]] = [
    ("/", ["profitChart", "dashboard.js", "dash-pie-canvas"], None),
    ("/sales/", ["salesPerformanceChart", "quickProductModal", "entity-form", "inventory-lines", "inventory-form.js"], None),
    ("/purchases/", ["purchasesPerformanceChart", "purchases-page-chart.js"], None),
    ("/account/", ["accountPerformanceChart", "setupModal", "takeModal"], None),
    ("/journal/", ["journalPerformanceChart", "journal-detail.js"], None),
    ("/expenses/", ["partyAccountPie", "formModal", "entity-form"], None),
    ("/vendors/", ["vendorListChart", "formModal", "entity-form"], None),
    ("/customers/", ["customerListChart", "formModal", "entity-form"], None),
    ("/inventory/", ["inventoryListChart", "formModal", "entity-form", "inventory-lines", "inventory-form.js"], None),
    ("/salesmen/", ["salesmanListChart", "formModal", "entity-form"], None),
    ("/categories/", ["formModal", "entity-form"], None),
    ("/auth/users", ["formModal", "entity-form"], UserRole.SUPER_ADMIN),
    ("/sync-backup/", [], None),
]

JS_MIN_BYTES = {
    "inventory-form.js": 3000,
    "sales.js": 5000,
    "dashboard.js": 500,
    "list-page-charts.js": 500,
    "category-lookup.js": 500,
    "vendor-lookup.js": 500,
}

PARTIAL_MIN_BYTES = {
    "partials/inventory_add_product_form.html": 200,
    "partials/macros.html": 500,
    "partials/confirm_modal.html": 100,
}


def main() -> int:
    app = create_app()
    failed: list[str] = []

    with app.app_context():
        cust = Customer.query.filter_by(is_deleted=False).first()
        vend = Vendor.query.filter_by(is_deleted=False).first()
        prod = Product.query.filter_by(is_deleted=False).first()
        sm = Salesman.query.first()
        user = User.query.filter_by(is_deleted=False).first()
        if not user:
            print("No user in DB — cannot test authenticated pages.")
            return 1

        checks: list[tuple[str, list[str], UserRole | None]] = list(PAGE_CHECKS)
        if cust:
            checks.append((f"/customers/{cust.id}", ["partyAccountPie"], None))
        if vend:
            checks.append((f"/vendors/{vend.id}", ["partyAccountPie"], None))
        if prod:
            checks.append((f"/inventory/products/{prod.id}", ["productPerformanceChart"], None))
        if sm:
            checks.append((f"/salesmen/{sm.id}", ["salesmanPerformanceChart"], None))

        templates_dir = ROOT / "app" / "templates"
        for rel, min_bytes in PARTIAL_MIN_BYTES.items():
            path = templates_dir / rel
            if not path.exists():
                failed.append(f"template {rel}: missing")
            elif path.stat().st_size < min_bytes:
                failed.append(f"template {rel}: too small ({path.stat().st_size} bytes)")

        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["_user_id"] = str(user.id)
                sess["_fresh"] = True

            for path, needles, required_role in checks:
                session_user = user
                if required_role == UserRole.SUPER_ADMIN:
                    admin = User.query.filter_by(
                        role=UserRole.SUPER_ADMIN, is_deleted=False
                    ).first()
                    if not admin:
                        print(f"Note: {path} skipped (no super admin in DB)")
                        continue
                    session_user = admin
                with client.session_transaction() as sess:
                    sess["_user_id"] = str(session_user.id)
                    sess["_fresh"] = True
                resp = client.get(path)
                html = resp.get_data(as_text=True)
                if resp.status_code != 200:
                    failed.append(f"{path}: HTTP {resp.status_code}")
                    continue
                missing = [n for n in needles if n not in html]
                if missing:
                    failed.append(f"{path}: missing {missing}")

            for js, min_bytes in JS_MIN_BYTES.items():
                resp = client.get(f"/static/js/{js}")
                if resp.status_code != 200:
                    failed.append(f"/static/js/{js}: HTTP {resp.status_code}")
                elif len(resp.data) < min_bytes:
                    failed.append(f"/static/js/{js}: too small ({len(resp.data)} bytes)")

            # Product lookup with active=active should not return inactive products
            resp = client.get("/api/products/lookup?active=active&q=")
            if resp.status_code != 200:
                failed.append(f"/api/products/lookup: HTTP {resp.status_code}")
            else:
                payload = resp.get_json() or {}
                rows = payload.get("results", payload if isinstance(payload, list) else [])
                inactive = [p for p in rows if isinstance(p, dict) and p.get("is_active") is False]
                if inactive:
                    failed.append("/api/products/lookup?active=active: returned inactive products")

            # Inventory create endpoint exists (POST without body -> validation error, not 404)
            resp = client.post(
                "/inventory/products/create",
                json={},
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code == 404:
                failed.append("/inventory/products/create: route missing")

            resp = client.post(
                "/api/products/batch",
                json={},
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code == 404:
                failed.append("/api/products/batch: route missing")

    if failed:
        print("App verification FAILED:")
        for line in failed:
            print(" -", line)
        return 1

    print(f"App verification OK — {len(checks)} pages, {len(JS_MIN_BYTES)} JS files, {len(PARTIAL_MIN_BYTES)} partials.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

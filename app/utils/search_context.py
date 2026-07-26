"""Map request path to page-local search (navbar filters only that page)."""

import re
from typing import NamedTuple


class SearchContext(NamedTuple):
    scope: str
    table_selector: str
    placeholder: str
    mode: str  # "table" | "api" | "none"


# Unmatched pages: no cross-module search.
_DEFAULT = SearchContext(
    scope="none",
    table_selector="",
    placeholder="Search is not available on this page",
    mode="none",
)

_RULES: list[tuple[re.Pattern[str], SearchContext]] = [
    # Dashboard — search everything, jump to the matching entry/page.
    (
        re.compile(r"^/$"),
        SearchContext(
            scope="global",
            table_selector="",
            placeholder="Search customers, products, sales, vendors…",
            mode="api",
        ),
    ),
    (
        re.compile(r"^/customers/\d+$"),
        SearchContext(
            scope="customer_ledger",
            table_selector="#customer-ledger-table",
            placeholder="Search this customer ledger…",
            mode="table",
        ),
    ),
    (
        re.compile(r"^/vendors/\d+$"),
        SearchContext(
            scope="vendor_ledger",
            table_selector="#vendor-ledger-table",
            placeholder="Search this vendor ledger…",
            mode="table",
        ),
    ),
    (
        re.compile(r"^/sales/pos$"),
        SearchContext(
            scope="inventory",
            table_selector="",
            placeholder="Search products…",
            mode="api",
        ),
    ),
    (
        re.compile(r"^/sales/\d+"),
        SearchContext(
            scope="sales",
            table_selector="",
            placeholder="Search sales…",
            mode="api",
        ),
    ),
    (
        re.compile(r"^/sales"),
        SearchContext(
            scope="sales",
            table_selector="#sales-table",
            placeholder="Search sales (invoice, customer, date…)…",
            mode="table",
        ),
    ),
    (
        re.compile(r"^/inventory/adjustments"),
        SearchContext(
            scope="inventory_adjustments",
            table_selector="#inventory-adjustments-table",
            placeholder="Search adjustments…",
            mode="table",
        ),
    ),
    (
        re.compile(r"^/inventory/\d+$"),
        SearchContext(
            scope="inventory_batches",
            table_selector="#inventory-batches-table",
            placeholder="Search batches on this product…",
            mode="table",
        ),
    ),
    (
        re.compile(r"^/inventory"),
        SearchContext(
            scope="inventory",
            table_selector="#inventory-products-table",
            placeholder="Search products (name, category…)…",
            mode="table",
        ),
    ),
    (
        re.compile(r"^/purchases"),
        SearchContext(
            scope="purchases",
            table_selector="#purchases-table",
            placeholder="Search purchases (invoice, vendor…)…",
            mode="table",
        ),
    ),
    (
        re.compile(r"^/expenses"),
        SearchContext(
            scope="expenses",
            table_selector="#expenses-table",
            placeholder="Search expenses (name, category…)…",
            mode="table",
        ),
    ),
    (
        re.compile(r"^/journal"),
        SearchContext(
            scope="journal",
            table_selector="#journal-table",
            placeholder="Search journal (type, party, reference…)…",
            mode="table",
        ),
    ),
    (
        re.compile(r"^/account"),
        SearchContext(
            scope="account",
            table_selector="#account-taken-table",
            placeholder="Search amount taken (name, date, amount…)…",
            mode="table",
        ),
    ),
    (
        re.compile(r"^/categories/\d+$"),
        SearchContext(
            scope="category_detail",
            table_selector="#category-subs-table",
            placeholder="Search subcategories…",
            mode="table",
        ),
    ),
    (
        re.compile(r"^/categories"),
        SearchContext(
            scope="categories",
            table_selector="#categories-table",
            placeholder="Search categories…",
            mode="table",
        ),
    ),
    (
        re.compile(r"^/customers"),
        SearchContext(
            scope="customers",
            table_selector="#customers-table",
            placeholder="Search customers (name, phone, book…)…",
            mode="table",
        ),
    ),
    (
        re.compile(r"^/vendors"),
        SearchContext(
            scope="vendors",
            table_selector="#vendors-table",
            placeholder="Search vendors (name, phone…)…",
            mode="table",
        ),
    ),
    (
        re.compile(r"^/auth/users"),
        SearchContext(
            scope="users",
            table_selector="#users-table",
            placeholder="Search users…",
            mode="table",
        ),
    ),
    (
        re.compile(r"^/sync-backup"),
        SearchContext(
            scope="maintenance",
            table_selector="#backups-table",
            placeholder="Search backups…",
            mode="table",
        ),
    ),
    (
        re.compile(r"^/reports/sales"),
        SearchContext(
            scope="reports_sales",
            table_selector="main .datatable",
            placeholder="Search sales report…",
            mode="table",
        ),
    ),
    (
        re.compile(r"^/reports/cashbook"),
        SearchContext(
            scope="reports_cashbook",
            table_selector="main .datatable",
            placeholder="Search cashbook…",
            mode="table",
        ),
    ),
    (
        re.compile(r"^/settings/audit"),
        SearchContext(
            scope="audit",
            table_selector="main .datatable",
            placeholder="Search audit log…",
            mode="table",
        ),
    ),
]


def resolve_search_context(path: str) -> SearchContext:
    path = (path or "/").split("?", 1)[0].rstrip("/") or "/"
    for pattern, ctx in _RULES:
        if pattern.match(path):
            return ctx
    return _DEFAULT

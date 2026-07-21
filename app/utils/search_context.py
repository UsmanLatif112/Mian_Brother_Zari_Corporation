"""Map request path to page-local search (navbar filters one table / API scope)."""

import re
from typing import NamedTuple


class SearchContext(NamedTuple):
    scope: str
    table_selector: str
    placeholder: str
    mode: str  # "table" | "api"


_DEFAULT = SearchContext(
    scope="global",
    table_selector="",
    placeholder="Search across customers, products, sales…",
    mode="api",
)

_RULES: list[tuple[re.Pattern[str], SearchContext]] = [
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
            placeholder="Search products (use POS search)…",
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
            table_selector="main .datatable",
            placeholder="Search adjustments…",
            mode="table",
        ),
    ),
    (
        re.compile(r"^/inventory"),
        SearchContext(
            scope="inventory",
            table_selector="main .datatable",
            placeholder="Search products (name, category…)…",
            mode="table",
        ),
    ),
    (
        re.compile(r"^/purchases"),
        SearchContext(
            scope="purchases",
            table_selector="main .datatable",
            placeholder="Search purchases (invoice, vendor…)…",
            mode="table",
        ),
    ),
    (
        re.compile(r"^/expenses"),
        SearchContext(
            scope="expenses",
            table_selector="main .datatable",
            placeholder="Search expenses (name, category…)…",
            mode="table",
        ),
    ),
    (
        re.compile(r"^/journal"),
        SearchContext(
            scope="journal",
            table_selector="main .datatable",
            placeholder="Search journal (type, party, reference…)…",
            mode="table",
        ),
    ),
    (
        re.compile(r"^/customers"),
        SearchContext(
            scope="customers",
            table_selector="main .datatable",
            placeholder="Search customers (name, phone, book…)…",
            mode="table",
        ),
    ),
    (
        re.compile(r"^/vendors"),
        SearchContext(
            scope="vendors",
            table_selector="main .datatable",
            placeholder="Search vendors (name, phone…)…",
            mode="table",
        ),
    ),
    (
        re.compile(r"^/auth/users"),
        SearchContext(
            scope="users",
            table_selector="main .datatable",
            placeholder="Search users…",
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

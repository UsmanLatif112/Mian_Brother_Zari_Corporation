"""Application version — bump when releasing a desktop update."""

APP_VERSION = "1.8.8"
APP_BUILD = 188

# Used by make_release_manifest when --notes is omitted.
RELEASE_NOTES = """- Inventory Profitability page: period filter, charts, category/product profit report
- Customer & vendor lists: Profit/Loss column + filter; profit vs loss shown separately
- Custom customer types from Categories; quick-add on customer/sale forms
- Optional due date on unpaid/partial sales and customer loans
- Due reminders: tomorrow green, due yellow, overdue red (receivable only)
- Clear Filters bar under topbar (no crowded inline header filters)
- Vendor balance column: amount only (color + tooltip); dashboard profit/loss cards fixed"""

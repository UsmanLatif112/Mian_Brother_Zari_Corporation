"""Application version — bump when releasing a desktop update."""

APP_VERSION = "1.6.9"
APP_BUILD = 169

# Used by make_release_manifest when --notes is omitted.
RELEASE_NOTES = """- Purchase entry: full sealed bags + loose kg (not one decimal like 12.5)
- Sale edit void: stock restores correctly; re-sale uses void layers first (no orphan stock)
- Recent movements: Void Reversal vs Sale Return In; Purchase/Reprice/Qty adjust labels & notes
- Existing void movements relabeled on startup (not customer returns)
- Inventory: Active/Inactive status (like customers/vendors); inactive hidden from sale modal
- Dashboard & list charts restored on Sales, Purchases, Account, Journal, Expenses, and more
- Customer/Vendor active filter defaults to All; filter by Active or Inactive as needed"""

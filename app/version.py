"""Application version — bump when releasing a desktop update."""

APP_VERSION = "1.6.7"
APP_BUILD = 167

# Used by make_release_manifest when --notes is omitted.
RELEASE_NOTES = """- Sales listing: item image column removed; separate Notes column (truncated with tooltip)
- Reprice / edit purchase price corrects sold cost of goods for that batch (dashboard Total Cost & profit)
- Sale price change still applies to remaining stock only
- Inventory movements: Reprice notes show cost old→new; Sale Out / Return notes update after reprice
- Customer & salesman ledger notes: Sale INV-… / item particulars (returns too)"""

"""Application version — bump when releasing a desktop update."""

APP_VERSION = "1.4.0"
APP_BUILD = 140

# Used by make_release_manifest when --notes is omitted.
RELEASE_NOTES = """- MySQL cloud sync: faster batched upload + 0–100% progress bar
- Sync Now / Cancel Sync with in-app modals (no browser alerts)
- Auto push every 15 minutes when online (local SQLite → cloud)
- Clean error messages (no SQL dumps for shop users)
- agency_id on all business tables (shop Admin = agency)
- Schema auto-heal when cloud columns are missing
- Desktop copy/paste, vendor/customer ledger edits, cascade tools from 1.3.x"""

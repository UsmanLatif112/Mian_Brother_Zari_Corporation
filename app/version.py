"""Application version — bump when releasing a desktop update."""

APP_VERSION = "1.7.1"
APP_BUILD = 171

# Used by make_release_manifest when --notes is omitted.
RELEASE_NOTES = """- Fixed Add Product modal on Inventory and Sales (form restored; nested modals work)
- Fixed Add Customer and Add Vendor from Sales/Inventory (CNIC field, field layout, quick-add API)
- Sale edit: stock restores correctly; movements show Sale Edit (not Void) with clear particulars
- Sale edit qty change shows one net row in Recent Movements (e.g. 1 → 2 shows −1, not two rows)
- Modal nesting fix so all Add buttons open correctly across the app
- Purchase entry: sealed bags + loose kg; void/reprice/movement labels improved
- Inventory Active/Inactive filter; charts restored on dashboard and list pages
- Faster login when offline; database migration preserves existing data"""

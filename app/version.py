"""Application version — bump when releasing a desktop update."""

APP_VERSION = "1.2.8"
APP_BUILD = 128

# Used by make_release_manifest when --notes is omitted.
RELEASE_NOTES = """- Company logo stored in main DB + staff package DB (bytes + file)
- Super Admin Users list backfills logos; packages rebuild after branding
- MySQL registry syncs company name / logo
- Agri Books branding; Sale Full / Open mode
- Journal / sales / purchases Particulars (name / units / weight)
- Purchase cash rule; Stock Value on Inventory
- Customer ledger & sales print improvements; journal print warning"""

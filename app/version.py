"""Application version — bump when releasing a desktop update."""

APP_VERSION = "1.9.0"
APP_BUILD = 190

# Used by make_release_manifest when --notes is omitted.
RELEASE_NOTES = """- Sync now: progress overlay, internet/MySQL checks, customers/vendors/ledgers + logo only
- Google Drive upload queue keeps last 5 pending (older dropped)
- Backup & Cloud Sync button simplified
- Company logo for Agri Books portal sidebar + favicon"""

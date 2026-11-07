"""Application version — bump when releasing a desktop update."""

APP_VERSION = "1.8.9"
APP_BUILD = 189

# Used by make_release_manifest when --notes is omitted.
RELEASE_NOTES = """- Backup & Cloud: Sync customers, vendors, ledgers + company logo to MySQL / online portal
- Sync no longer uploads full ERP (inventory/sales stay local)
- Company logo pushed for Agri Books portal sidebar + favicon
- Online portal domain: agribooks.udottechnologies.com"""

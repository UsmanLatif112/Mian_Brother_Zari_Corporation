"""Application version — bump when releasing a desktop update."""

APP_VERSION = "1.9.3"
APP_BUILD = 193

# Used by make_release_manifest when --notes is omitted.
RELEASE_NOTES = """- Fix customer/vendor profit: period filter no longer hides parties by join date
- List columns: Status → Profit/Loss → Balance
- Sync: customers/vendors/ledgers + profit metrics (logos via Super Admin online)"""

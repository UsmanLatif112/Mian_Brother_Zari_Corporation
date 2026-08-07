"""Application version — bump when releasing a desktop update."""

APP_VERSION = "1.6.3"
APP_BUILD = 163

# Used by make_release_manifest when --notes is omitted.
RELEASE_NOTES = """- Customer list: Old Balance + Total Balance (old + sales/payments)
- Customer ledger: Opening entry with date when old account is set
- Editing old account balance syncs Opening ledger row and total balance
- Customer ledger sorted newest first
- Salesmen (field officers): optional on each sale — name, phone, company, photo, opening balance
- Salesman list + ledger: invoice, particulars, Paid/Partial/Unpaid, debit/credit/balance
- One product = one fixed unit weight; open leftover kg first"""

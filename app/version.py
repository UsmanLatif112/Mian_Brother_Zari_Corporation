"""Application version — bump when releasing a desktop update."""

APP_VERSION = "1.6.0"
APP_BUILD = 160

# Used by make_release_manifest when --notes is omitted.
RELEASE_NOTES = """- Inventory Add Product: each row creates its own purchase + vendor ledger line
- Vendor ledger Particulars: product / qty × unit weight @ rate
- Sale: choose bag weight when product has multiple sizes (FIFO per weight)
- Each weight keeps its own stock queue and sale price
- Unit Wt dropdown UI fixed on sale lines
- Sealed bags + open kg stock display; unit weight stored per batch
- Multi-product inventory add from one vendor invoice"""

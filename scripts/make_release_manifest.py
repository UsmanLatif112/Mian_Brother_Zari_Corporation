"""Write version.json for hosting update manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.version import APP_BUILD, APP_VERSION


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate version.json for MBF ERP updates")
    parser.add_argument("zip_path", help="Path to release zip file")
    parser.add_argument(
        "--base-url",
        default="http://mbfupdates.usmanlateef.com",
        help="Public URL where zip and version.json are hosted",
    )
    parser.add_argument("--notes", default="", help="Release notes text")
    parser.add_argument("-o", "--output", default="", help="Output path for version.json")
    args = parser.parse_args()

    zip_path = Path(args.zip_path).resolve()
    if not zip_path.is_file():
        raise SystemExit(f"Zip not found: {zip_path}")

    base = args.base_url.rstrip("/")
    zip_name = zip_path.name
    manifest = {
        "version": APP_VERSION,
        "build": APP_BUILD,
        "released_at": date.today().isoformat(),
        "download_url": f"{base}/{zip_name}",
        "sha256": sha256_file(zip_path),
        "release_notes": args.notes
        or "- Registration and working date\n- Auto-update support\n- UI improvements",
    }

    out = Path(args.output) if args.output else zip_path.parent / "version.json"
    out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out}")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()

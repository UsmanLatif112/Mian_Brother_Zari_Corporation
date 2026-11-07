"""Desktop app update checks and safe apply (never touches data/ folder)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from app.runtime_paths import app_home, is_frozen, user_data_dir
from app.version import APP_BUILD, APP_VERSION

logger = logging.getLogger(__name__)

MANIFEST_TIMEOUT = 12
DOWNLOAD_TIMEOUT = 600
SKIP_FILE = "update_skip_build.txt"
PENDING_FILE = "update_pending.json"


def manifest_url() -> str | None:
    url = (os.environ.get("UPDATE_MANIFEST_URL") or "").strip()
    return url or None


def updates_enabled() -> bool:
    return bool(manifest_url())


def is_desktop_app() -> bool:
    return os.environ.get("DESKTOP_APP", "").lower() in ("1", "true", "yes")


def can_apply_updates() -> bool:
    return is_desktop_app() and is_frozen()


def current_version_info() -> dict[str, Any]:
    return {
        "version": APP_VERSION,
        "build": APP_BUILD,
        "desktop": is_desktop_app(),
        "can_apply": can_apply_updates(),
        "manifest_configured": updates_enabled(),
    }


def _skip_path() -> Path:
    return Path(user_data_dir()) / SKIP_FILE


def get_skipped_build() -> int:
    path = _skip_path()
    if not path.is_file():
        return 0
    try:
        return int(path.read_text(encoding="utf-8").strip() or "0")
    except (OSError, ValueError):
        return 0


def set_skipped_build(build: int) -> None:
    _skip_path().write_text(str(int(build)), encoding="utf-8")


def _fetch_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": f"MBF-ERP/{APP_VERSION} (build {APP_BUILD})"},
    )
    with urllib.request.urlopen(req, timeout=MANIFEST_TIMEOUT) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Invalid update manifest.")
    return data


def _parse_manifest(data: dict[str, Any]) -> dict[str, Any]:
    version = str(data.get("version") or "").strip() or "0.0.0"
    try:
        build = int(data.get("build") or 0)
    except (TypeError, ValueError):
        build = 0
    download_url = str(data.get("download_url") or "").strip()
    if not download_url:
        raise ValueError("Update manifest is missing download_url.")
    return {
        "version": version,
        "build": build,
        "download_url": download_url,
        "sha256": str(data.get("sha256") or "").strip().lower(),
        "release_notes": str(data.get("release_notes") or "").strip(),
        "released_at": str(data.get("released_at") or "").strip(),
    }


def check_for_update(*, respect_skip: bool = True) -> dict[str, Any]:
    base = current_version_info()
    url = manifest_url()
    if not url:
        return {
            **base,
            "ok": True,
            "configured": False,
            "online": None,
            "update_available": False,
            "message": "Update server is not configured.",
        }

    try:
        manifest = _parse_manifest(_fetch_json(url))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        logger.info("Update check failed: %s", exc)
        return {
            **base,
            "ok": False,
            "configured": True,
            "online": False,
            "update_available": False,
            "error": "Could not reach update server. Check your internet connection.",
        }

    latest_build = int(manifest["build"])
    skipped = get_skipped_build() if respect_skip else 0
    available = latest_build > APP_BUILD and latest_build != skipped

    return {
        **base,
        "ok": True,
        "configured": True,
        "online": True,
        "update_available": available,
        "latest_version": manifest["version"],
        "latest_build": latest_build,
        "download_url": manifest["download_url"],
        "release_notes": manifest["release_notes"],
        "released_at": manifest["released_at"],
        "skipped_build": skipped,
        "message": (
            f"Version {manifest['version']} is available."
            if available
            else "You are on the latest version."
        ),
    }


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _download_file(url: str, dest: Path, progress_cb=None) -> None:
    """Download url to dest; optional progress_cb(dict) for UI updates."""
    for _evt in _iter_download_file(url, dest):
        if progress_cb:
            progress_cb(_evt)


def _iter_download_file(url: str, dest: Path):
    """Yield progress dicts while downloading url to dest."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": f"MBF-ERP/{APP_VERSION} (build {APP_BUILD})"},
    )
    with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp, dest.open("wb") as out:
        total = 0
        try:
            total = int(resp.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            total = 0
        downloaded = 0
        chunk_size = 1024 * 256
        last_pct = -1
        while True:
            chunk = resp.read(chunk_size)
            if not chunk:
                break
            out.write(chunk)
            downloaded += len(chunk)
            if total > 0:
                pct = min(99, int(downloaded * 100 / total))
            else:
                pct = min(90, 5 + int((downloaded / (1024 * 1024)) * 3))
            if pct != last_pct or downloaded == total:
                last_pct = pct
                yield {
                    "phase": "download",
                    "percent": pct,
                    "bytes_downloaded": downloaded,
                    "bytes_total": total,
                    "message": _download_message(downloaded, total),
                }
        yield {
            "phase": "download",
            "percent": 100 if total > 0 else min(95, max(last_pct, 0) + 5),
            "bytes_downloaded": downloaded,
            "bytes_total": total or downloaded,
            "message": "Download complete. Verifying...",
        }


def _download_message(downloaded: int, total: int) -> str:
    def _mb(n: int) -> str:
        return f"{n / (1024 * 1024):.1f} MB"

    if total > 0:
        return f"Downloading update… {_mb(downloaded)} / {_mb(total)}"
    return f"Downloading update… {_mb(downloaded)}"


def _find_app_root(extract_dir: Path) -> Path:
    exe_name = "MianBrotherFertilizers.exe"
    direct = extract_dir / exe_name
    if direct.is_file():
        return extract_dir
    for path in extract_dir.rglob(exe_name):
        return path.parent
    raise ValueError("Downloaded update does not contain MianBrotherFertilizers.exe.")


def _write_apply_script(source_dir: Path, dest_dir: Path) -> Path:
    bat = dest_dir / "_apply_update.bat"
    src = str(source_dir.resolve())
    dst = str(dest_dir.resolve())
    lines = [
        "@echo off",
        "setlocal",
        f'set "SRC={src}"',
        f'set "DEST={dst}"',
        "echo Applying MBF ERP update...",
        "echo Your data folder will NOT be changed.",
        "timeout /t 2 /nobreak >nul",
        ":waitloop",
        'tasklist /FI "IMAGENAME eq MianBrotherFertilizers.exe" 2>nul | find /I "MianBrotherFertilizers.exe" >nul',
        "if %ERRORLEVEL%==0 (timeout /t 1 /nobreak >nul & goto waitloop)",
        'robocopy "%SRC%" "%DEST%" /E /XD data /NFL /NDL /NJH /NJS /nc /ns /np',
        "if exist \"%DEST%\\START_HERE.bat\" (",
        '  start "" "%DEST%\\START_HERE.bat"',
        ") else (",
        '  start "" "%DEST%\\MianBrotherFertilizers.exe"',
        ")",
        "endlocal",
        "del \"%~f0\"",
    ]
    bat.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    return bat


def prepare_update_apply():
    """Download and stage update; returns final result dict (no streaming)."""
    result = {"ok": False, "error": "Update failed."}
    for event in iter_update_apply_progress():
        if event.get("phase") == "done" or event.get("phase") == "error":
            result = event
    return result


def iter_update_apply_progress():
    """Yield NDJSON-friendly progress events, then a final done/error event."""
    if not can_apply_updates():
        yield {
            "phase": "error",
            "ok": False,
            "percent": 0,
            "error": "Updates can only be applied from the desktop app (.exe).",
        }
        return

    yield {
        "phase": "check",
        "percent": 1,
        "message": "Checking update server…",
        "ok": True,
    }

    status = check_for_update(respect_skip=False)
    if not status.get("ok"):
        yield {
            "phase": "error",
            "ok": False,
            "offline": True,
            "percent": 0,
            "error": status.get("error", "Update check failed."),
        }
        return
    if not status.get("update_available"):
        yield {
            "phase": "error",
            "ok": False,
            "percent": 0,
            "error": "No newer update is available.",
        }
        return

    url = manifest_url()
    assert url
    try:
        manifest = _parse_manifest(_fetch_json(url))
    except Exception as exc:
        yield {
            "phase": "error",
            "ok": False,
            "offline": True,
            "percent": 0,
            "error": str(exc) or "Could not read update manifest.",
        }
        return

    download_url = manifest["download_url"]
    expected_sha = manifest.get("sha256") or ""

    work_root = Path(tempfile.mkdtemp(prefix="mbf-update-"))
    zip_path = work_root / "update.zip"
    extract_dir = work_root / "extract"
    extract_dir.mkdir(parents=True, exist_ok=True)

    try:
        yield {
            "phase": "download",
            "percent": 5,
            "message": "Starting download…",
            "ok": True,
        }

        for evt in _iter_download_file(download_url, zip_path):
            raw = int(evt.get("percent") or 0)
            overall = 5 + int(raw * 0.75)
            yield {
                **evt,
                "percent": overall,
                "ok": True,
            }

        yield {
            "phase": "verify",
            "percent": 82,
            "message": "Verifying download…",
            "ok": True,
        }
        if expected_sha:
            actual = _sha256_file(zip_path)
            if actual != expected_sha:
                raise ValueError("Downloaded update failed security check (checksum mismatch).")

        yield {
            "phase": "extract",
            "percent": 88,
            "message": "Extracting update files…",
            "ok": True,
        }
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)

        yield {
            "phase": "prepare",
            "percent": 96,
            "message": "Preparing installer…",
            "ok": True,
        }
        source_root = _find_app_root(extract_dir)
        dest_root = Path(app_home())
        bat_path = _write_apply_script(source_root, dest_root)

        pending = {
            "version": manifest["version"],
            "build": manifest["build"],
            "source": str(source_root),
            "work_root": str(work_root),
            "bat": str(bat_path),
        }
        pending_path = Path(user_data_dir()) / PENDING_FILE
        pending_path.write_text(json.dumps(pending, indent=2), encoding="utf-8")

        yield {
            "phase": "done",
            "ok": True,
            "percent": 100,
            "message": "Update downloaded. The app will close and restart automatically.",
            "version": manifest["version"],
            "build": manifest["build"],
            "bat_path": str(bat_path),
        }
    except Exception as exc:
        logger.exception("Prepare update failed")
        shutil.rmtree(work_root, ignore_errors=True)
        yield {
            "phase": "error",
            "ok": False,
            "percent": 0,
            "error": str(exc),
        }


def launch_pending_update() -> bool:
    """Run apply script after the desktop window closes."""
    pending_path = Path(user_data_dir()) / PENDING_FILE
    if not pending_path.is_file():
        return False
    try:
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
        bat = Path(str(pending.get("bat") or ""))
        if not bat.is_file():
            pending_path.unlink(missing_ok=True)
            return False
        os.startfile(str(bat))  # noqa: S606 — Windows only desktop updater
        return True
    except Exception:
        logger.exception("Could not launch pending update")
        return False
    finally:
        pending_path.unlink(missing_ok=True)

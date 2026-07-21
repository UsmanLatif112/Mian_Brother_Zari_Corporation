"""
Desktop launcher for Mian Brother Fertilizers ERP.
Starts the local server and opens a native window (no browser required).
"""

from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
import traceback


def _prepare_env() -> None:
    os.environ.setdefault("DESKTOP_APP", "true")
    os.environ.setdefault("FLASK_ENV", "production")
    os.environ.setdefault("OFFLINE_FIRST", "true")
    os.environ.setdefault("DATABASE_MODE", "sqlite")
    os.environ.setdefault("SYNC_AUTO_ENABLED", "false")
    # Stable secret for local desktop sessions
    os.environ.setdefault("SECRET_KEY", "mian-brother-fertilizers-desktop-local-key")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _init_database(app) -> None:
    from app.extensions import db
    from app.services.dashboard_service import ensure_customer_type_column
    from app.utils.seed import seed_database

    with app.app_context():
        db.create_all()
        try:
            ensure_customer_type_column()
        except Exception:
            logging.getLogger(__name__).warning("Schema ensure skipped", exc_info=True)
        seed_database()


def main() -> int:
    _prepare_env()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        import webview
        from waitress import serve

        from app import create_app
        from app.runtime_paths import APP_DISPLAY_NAME
    except Exception:
        _show_error("Missing desktop dependencies", traceback.format_exc())
        return 1

    try:
        app = create_app()
        _init_database(app)
    except Exception:
        _show_error("Could not start application", traceback.format_exc())
        return 1

    port = _free_port()
    host = "127.0.0.1"
    url = f"http://{host}:{port}/"

    def run_server() -> None:
        serve(app, host=host, port=port, threads=8)

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()

    # Wait until server accepts connections
    for _ in range(50):
        try:
            with socket.create_connection((host, port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.1)
    else:
        _show_error("Server did not start", "Local server failed to open a port.")
        return 1

    webview.create_window(
        APP_DISPLAY_NAME,
        url,
        width=1360,
        height=860,
        min_size=(1024, 700),
        confirm_close=True,
    )
    # Windows window/taskbar icon comes from the .exe (PyInstaller); Edge WebView2 hosts UI
    try:
        webview.start(gui="edgechromium")
    except Exception:
        webview.start()
    return 0


def _show_error(title: str, detail: str) -> None:
    logging.error("%s\n%s", title, detail)
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, detail[:1500], title, 0x10)
    except Exception:
        print(title, detail, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())

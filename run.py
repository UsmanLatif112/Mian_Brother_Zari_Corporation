import os
import sys

from dotenv import load_dotenv

# Always load project .env (cPanel cwd is not always the app root)
_BASE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_BASE, ".env"))

_mode = (os.environ.get("APP_MODE") or "").strip().lower()
if _mode in ("online", "host", "cloud", "web"):
    # Match passenger_wsgi defaults so `python run.py` / flask CLI use MySQL
    os.environ.setdefault("FLASK_ENV", "production")
    os.environ.setdefault("OFFLINE_FIRST", "false")
    os.environ.setdefault("DATABASE_MODE", "production")
    os.environ.setdefault("SYNC_AUTO_ENABLED", "false")
    os.environ.setdefault("AUTO_BACKUP_ENABLED", "false")

from app import create_app

app = create_app()

if __name__ == "__main__":
    uri = app.config.get("SQLALCHEMY_DATABASE_URI") or ""
    dialect = uri.split(":", 1)[0] if uri else "unknown"
    print(f"DB engine: {dialect} | APP_MODE={os.environ.get('APP_MODE', '')!r}", file=sys.stderr)
    if dialect.startswith("sqlite"):
        print(
            "WARNING: using SQLite. For cPanel online set APP_MODE=online and "
            "MYSQL_DATABASE_URI in .env (see .env.online.example).",
            file=sys.stderr,
        )
    debug = os.environ.get("FLASK_ENV", "development") != "production"
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=debug)

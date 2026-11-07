import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _resolve_database_uri() -> str:
    """MySQL for production; local SQLite when USE_LOCAL_SQLITE=true (dev)."""
    use_local = (os.environ.get("USE_LOCAL_SQLITE") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if use_local:
        local = (os.environ.get("LOCAL_SQLITE_URI") or "").strip()
        if local:
            return local
        db_path = ROOT / "instance" / "portal_local.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{db_path.as_posix()}"
    return (os.environ.get("MYSQL_DATABASE_URI") or "").strip()


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or "dev-portal-secret"
    SQLALCHEMY_DATABASE_URI = _resolve_database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    _uri = SQLALCHEMY_DATABASE_URI or ""
    if _uri.startswith("sqlite"):
        SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"check_same_thread": False}}
    else:
        SQLALCHEMY_ENGINE_OPTIONS = {
            "pool_pre_ping": True,
            "pool_recycle": 28000,
            "connect_args": {"connect_timeout": 10},
        }
    WTF_CSRF_ENABLED = True
    UPLOAD_FOLDER = str(ROOT / "uploads")
    RELEASES_FOLDER = str(ROOT / "uploads" / "releases")
    SQLITE_PACKAGES_FOLDER = str(ROOT / "uploads" / "sqlite_packages")
    MAX_CONTENT_LENGTH = 512 * 1024 * 1024  # app zip + sqlite
    SUITE_NAME = "Agri Books"
    PORTAL_DOMAIN = os.environ.get("PORTAL_DOMAIN") or "agribooks.udottechnologies.com"
    DEVELOPER_NAME = "U. Technologies"
    DEVELOPER_URL = "https://udottechnologies.com/"
    # Public update manifest URL (desktop apps will point here)
    UPDATE_MANIFEST_URL = (
        os.environ.get("UPDATE_MANIFEST_URL")
        or "https://agribooks.udottechnologies.com/updates/version.json"
    )

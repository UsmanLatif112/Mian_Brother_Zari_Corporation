import os
from datetime import timedelta
from urllib.parse import quote_plus

basedir = os.path.abspath(os.path.dirname(__file__))


def build_mysql_uri(
    user: str,
    password: str,
    host: str,
    port: int | str,
    database: str,
) -> str:
    return (
        f"mysql+pymysql://{quote_plus(user)}:{quote_plus(password)}"
        f"@{host}:{port}/{database}"
    )


def resolve_sqlalchemy_uri() -> str:
    """Offline-first: always SQLite locally unless production server deploy."""
    if os.environ.get("DESKTOP_APP", "").lower() in ("1", "true", "yes"):
        return os.environ.get("SQLITE_DATABASE_URI", "sqlite:///mbzc_erp.db")

    offline_first = os.environ.get("OFFLINE_FIRST", "true").lower() == "true"
    if offline_first and os.environ.get("FLASK_ENV", "development") != "production":
        return os.environ.get("SQLITE_DATABASE_URI", "sqlite:///mbzc_erp.db")

    mode = os.environ.get("DATABASE_MODE", "sqlite").lower()
    if mode == "test":
        uri = os.environ.get("MYSQL_TEST_DATABASE_URI")
        if uri:
            return uri
    if mode in ("mysql", "production"):
        uri = os.environ.get("MYSQL_DATABASE_URI")
        if uri:
            return uri
    return os.environ.get("SQLITE_DATABASE_URI", "sqlite:///mbzc_erp.db")


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 28000}
    PERMANENT_SESSION_LIFETIME = timedelta(hours=12)
    WTF_CSRF_ENABLED = True
    BACKUP_DIR = os.environ.get("BACKUP_DIR") or os.path.join(basedir, "backups")
    SYNC_AUTO_ENABLED = os.environ.get("SYNC_AUTO_ENABLED", "false").lower() == "true"
    SYNC_INTERVAL_MINUTES = int(os.environ.get("SYNC_INTERVAL_MINUTES", "15"))
    # Keep only N newest push records in sync_logs table (UI + table retention)
    SYNC_LOG_KEEP = int(os.environ.get("SYNC_LOG_KEEP", "5"))
    # Short MySQL probe — keeps Sync page + cron from hanging when offline
    SYNC_MYSQL_CONNECT_TIMEOUT = int(os.environ.get("SYNC_MYSQL_CONNECT_TIMEOUT", "2"))
    SYNC_MYSQL_CACHE_SECONDS = int(os.environ.get("SYNC_MYSQL_CACHE_SECONDS", "45"))
    # Multi-row UPSERT batch size (higher = fewer round-trips, faster push)
    SYNC_PUSH_BATCH_SIZE = int(os.environ.get("SYNC_PUSH_BATCH_SIZE", "200"))
    SYNC_MYSQL_READ_TIMEOUT = int(os.environ.get("SYNC_MYSQL_READ_TIMEOUT", "120"))
    SYNC_MYSQL_WRITE_TIMEOUT = int(os.environ.get("SYNC_MYSQL_WRITE_TIMEOUT", "120"))
    # Separate cron: SQLite auto-backup (all files kept on disk; UI shows last N)
    AUTO_BACKUP_ENABLED = os.environ.get("AUTO_BACKUP_ENABLED", "true").lower() == "true"
    AUTO_BACKUP_INTERVAL_HOURS = int(os.environ.get("AUTO_BACKUP_INTERVAL_HOURS", "1"))
    BACKUP_UI_LIMIT = int(os.environ.get("BACKUP_UI_LIMIT", "10"))
    # Google Drive cloud backup (replaces MySQL sync for desktop/offline-first)
    GOOGLE_DRIVE_CLIENT_ID = os.environ.get("GOOGLE_DRIVE_CLIENT_ID", "")
    GOOGLE_DRIVE_CLIENT_SECRET = os.environ.get("GOOGLE_DRIVE_CLIENT_SECRET", "")
    GOOGLE_DRIVE_REDIRECT_URI = os.environ.get("GOOGLE_DRIVE_REDIRECT_URI", "")
    GOOGLE_DRIVE_AUTO_UPLOAD = os.environ.get("GOOGLE_DRIVE_AUTO_UPLOAD", "true").lower() == "true"
    GOOGLE_DRIVE_RETENTION = int(os.environ.get("GOOGLE_DRIVE_RETENTION", "10"))
    GOOGLE_DRIVE_QUEUE_INTERVAL_MINUTES = int(os.environ.get("GOOGLE_DRIVE_QUEUE_INTERVAL_MINUTES", "5"))
    UPLOAD_FOLDER = os.path.join(basedir, "app", "static", "uploads")
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024
    DATABASE_MODE = os.environ.get("DATABASE_MODE", "sqlite")
    MYSQL_HOST = os.environ.get("MYSQL_HOST", "127.0.0.1")
    OFFLINE_FIRST = os.environ.get("OFFLINE_FIRST", "true").lower() == "true"
    # Shop data push target: production | test (registry always MYSQL_DATABASE_URI)
    SYNC_MYSQL_TARGET = os.environ.get("SYNC_MYSQL_TARGET", "test")
    UPDATE_MANIFEST_URL = os.environ.get("UPDATE_MANIFEST_URL", "")
    # desktop | online — online = hosted browser ERP on MySQL (agency multi-tenant)
    APP_MODE = (os.environ.get("APP_MODE") or "desktop").strip().lower()
    ONLINE_REQUIRE_REGISTERED = (
        os.environ.get("ONLINE_REQUIRE_REGISTERED", "true").lower() == "true"
    )


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False


config_by_name = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}


def get_config():
    env = os.environ.get("FLASK_ENV", "development")
    return config_by_name.get(env, DevelopmentConfig)


def apply_database_uri(app) -> None:
    if app.config.get("TESTING"):
        return
    if os.environ.get("DESKTOP_APP", "").lower() in ("1", "true", "yes"):
        app.config["SQLALCHEMY_DATABASE_URI"] = resolve_sqlalchemy_uri()
        return
    mode = (os.environ.get("APP_MODE") or "").strip().lower()
    if mode in ("online", "host", "cloud", "web"):
        uri = (
            (os.environ.get("MYSQL_DATABASE_URI") or "").strip()
            or (os.environ.get("MYSQL_TEST_DATABASE_URI") or "").strip()
        )
        if not uri:
            raise RuntimeError(
                "APP_MODE=online requires MYSQL_DATABASE_URI in .env "
                "(see .env.online.example). App refused to fall back to SQLite."
            )
        if not uri.startswith("mysql"):
            raise RuntimeError(
                "APP_MODE=online requires a mysql+pymysql:// URI, got: "
                f"{uri.split('://', 1)[0]}://"
            )
        app.config["SQLALCHEMY_DATABASE_URI"] = uri
        app.config["APP_MODE"] = "online"
        app.config["OFFLINE_FIRST"] = False
        return
    if os.environ.get("FLASK_ENV") == "production":
        app.config["SQLALCHEMY_DATABASE_URI"] = (
            os.environ.get("MYSQL_DATABASE_URI") or resolve_sqlalchemy_uri()
        )
    else:
        app.config["SQLALCHEMY_DATABASE_URI"] = resolve_sqlalchemy_uri()

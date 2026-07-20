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
    UPLOAD_FOLDER = os.path.join(basedir, "app", "static", "uploads")
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024
    DATABASE_MODE = os.environ.get("DATABASE_MODE", "sqlite")
    MYSQL_HOST = os.environ.get("MYSQL_HOST", "127.0.0.1")
    OFFLINE_FIRST = os.environ.get("OFFLINE_FIRST", "true").lower() == "true"
    SYNC_MYSQL_TARGET = os.environ.get("SYNC_MYSQL_TARGET", "production")


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
    if os.environ.get("FLASK_ENV") == "production":
        app.config["SQLALCHEMY_DATABASE_URI"] = (
            os.environ.get("MYSQL_DATABASE_URI") or resolve_sqlalchemy_uri()
        )
    else:
        app.config["SQLALCHEMY_DATABASE_URI"] = resolve_sqlalchemy_uri()

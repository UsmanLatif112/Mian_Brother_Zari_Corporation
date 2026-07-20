import os
import shutil
from datetime import datetime

from flask import current_app

from app.extensions import db


def ensure_backup_dir():
    path = current_app.config["BACKUP_DIR"]
    if not os.path.isabs(path):
        from config import basedir

        path = os.path.join(basedir, path)
        current_app.config["BACKUP_DIR"] = path
    os.makedirs(path, exist_ok=True)
    return path


def _sqlite_db_path() -> str:
    uri = current_app.config["SQLALCHEMY_DATABASE_URI"] or ""
    if not uri.startswith("sqlite"):
        raise ValueError("Current database is not SQLite")

    # Prefer the path SQLAlchemy is actually using
    try:
        db_file = db.engine.url.database
        if db_file and db_file != ":memory:":
            if os.path.isabs(db_file):
                if os.path.isfile(db_file):
                    return db_file
            else:
                for candidate in (
                    os.path.abspath(db_file),
                    os.path.join(current_app.instance_path, db_file),
                    os.path.join(current_app.instance_path, os.path.basename(db_file)),
                    os.path.join(current_app.root_path, db_file),
                    os.path.join(os.path.dirname(current_app.root_path), os.path.basename(db_file)),
                ):
                    if os.path.isfile(candidate):
                        return candidate
    except Exception:
        pass

    # Fallback: parse URI
    rel = uri.replace("sqlite:///", "")
    candidates = [
        rel,
        os.path.join(current_app.instance_path, rel),
        os.path.join(current_app.instance_path, os.path.basename(rel)),
        os.path.join(os.path.dirname(current_app.root_path), os.path.basename(rel)),
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return os.path.abspath(candidate)

    raise FileNotFoundError(
        "SQLite database file not found. Run `flask --app run init-db` from the project root."
    )


def backup_sqlite():
    backup_dir = ensure_backup_dir()
    src = _sqlite_db_path()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(backup_dir, f"sqlite_backup_{stamp}.db")
    shutil.copy2(src, dest)
    return dest


def restore_sqlite(backup_path: str):
    if not os.path.isfile(backup_path):
        raise FileNotFoundError(backup_path)
    dest = _sqlite_db_path()
    shutil.copy2(backup_path, dest)
    db.session.remove()
    db.engine.dispose()

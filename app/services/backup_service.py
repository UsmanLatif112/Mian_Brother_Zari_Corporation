import os
import shutil
import sqlite3
import time
import zipfile
from datetime import datetime

from flask import current_app
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

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


def _sidecar_paths(db_path: str) -> list[str]:
    return [db_path + "-wal", db_path + "-shm"]


def _remove_sidecars(db_path: str) -> None:
    for path in _sidecar_paths(db_path):
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass


def prepare_sqlite_for_export(*, checkpoint: bool = True) -> None:
    """
    Flush ORM; optionally run a non-blocking WAL checkpoint.

    Used by backup and by Push to MySQL (manual + cron).
    Does NOT call session.remove() — that would detach flask-login's User.

    checkpoint=False for backups (SQLite online backup already includes WAL).
    checkpoint=True for MySQL push uses PASSIVE (never waits for exclusive lock).
    """
    try:
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
    if not checkpoint:
        return
    try:
        # PASSIVE never blocks other writers; FULL was freezing backups for 30s+
        with db.engine.connect() as conn:
            conn.execute(text("PRAGMA busy_timeout=1000"))
            conn.execute(text("PRAGMA wal_checkpoint(PASSIVE)"))
            conn.commit()
    except Exception:
        pass


def _sqlite_online_backup(src_path: str, dest_path: str) -> None:
    """Consistent snapshot via SQLite Online Backup API (includes WAL pages)."""
    parent = os.path.dirname(dest_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if os.path.isfile(dest_path):
        os.remove(dest_path)

    abs_src = os.path.abspath(src_path).replace("\\", "/")
    src_conn = None
    try:
        # Prefer read-only so we never take a write lock on the live DB
        src_conn = sqlite3.connect(
            f"file:{abs_src}?mode=ro",
            uri=True,
            timeout=5,
            check_same_thread=False,
        )
    except sqlite3.Error:
        src_conn = sqlite3.connect(src_path, timeout=5, check_same_thread=False)

    try:
        src_conn.execute("PRAGMA busy_timeout=5000")
        dest_conn = sqlite3.connect(dest_path, timeout=5)
        try:
            dest_conn.execute("PRAGMA busy_timeout=5000")
            with dest_conn:
                src_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        src_conn.close()


def commit_after_backup(max_attempts: int = 6) -> None:
    """
    Commit audit / post-backup writes with busy retries.
    Backup files are already on disk — never treat lock errors as backup failure.
    """
    last_exc = None
    for attempt in range(max_attempts):
        try:
            db.session.execute(text("PRAGMA busy_timeout=8000"))
            db.session.commit()
            return
        except OperationalError as exc:
            last_exc = exc
            try:
                db.session.rollback()
            except Exception:
                pass
            msg = str(exc).lower()
            if "locked" not in msg and "busy" not in msg:
                raise
            time.sleep(0.12 * (attempt + 1))
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass
            raise
    if last_exc:
        raise last_exc


def _backup_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _write_backup_info(folder: str, stamp: str, files: list[str]) -> None:
    info_path = os.path.join(folder, "BACKUP_INFO.txt")
    lines = [
        "Mian Brother Fertilizers — SQLite backup",
        f"Created: {stamp}",
        f"Folder: {os.path.basename(folder)}",
        "",
        "Files in this backup:",
        *[f"  - {name}" for name in files],
        "",
        "Primary restore file: the .db (SQLite online backup — includes WAL data).",
        "Optional companions .db-wal / .db-shm are copied when safe; not required to restore.",
        "",
    ]
    with open(info_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def backup_sqlite(dest_path: str | None = None) -> str:
    """
    Create a dated backup folder containing the full SQLite set:

      backups/sqlite_backup_YYYYMMDD_HHMMSS/
        mbzc_erp.db          (consistent online-backup snapshot)
        mbzc_erp.db-wal      (if present)
        mbzc_erp.db-shm      (if present)
        BACKUP_INFO.txt

    Manual Create Backup and auto-backup both use this.
    If dest_path is set, also copy the folder (or a zip) there for the user.
    Returns the app backup folder path.
    """
    # Commit only — skip FULL checkpoint (that was the long wait).
    # Online backup API already merges WAL into the snapshot .db.
    prepare_sqlite_for_export(checkpoint=False)

    backup_dir = ensure_backup_dir()
    src = _sqlite_db_path()
    db_name = os.path.basename(src) or "mbzc_erp.db"
    stamp = _backup_stamp()
    folder_name = f"sqlite_backup_{stamp}"
    folder = os.path.join(backup_dir, folder_name)
    os.makedirs(folder, exist_ok=True)

    # 1) Consistent main DB (includes WAL contents) — usually <1s for this DB size
    consistent_db = os.path.join(folder, db_name)
    _sqlite_online_backup(src, consistent_db)
    saved = [db_name]

    # 2) Best-effort companion copies AFTER releasing backup connections.
    #    Skip if locked — the .db alone is enough to restore.
    for suffix in ("-wal", "-shm"):
        live = src + suffix
        if not os.path.isfile(live):
            continue
        dest_side = os.path.join(folder, db_name + suffix)
        try:
            shutil.copyfile(live, dest_side)
            saved.append(db_name + suffix)
        except OSError:
            pass

    _write_backup_info(folder, stamp, saved)

    # Clear any stale request transaction so later audit commits can proceed
    try:
        db.session.rollback()
    except Exception:
        pass

    if dest_path:
        _export_backup_folder(folder, dest_path)
        return os.path.abspath(str(dest_path).strip())
    return folder


def _export_backup_folder(folder: str, dest_path: str) -> str:
    """
    Copy backup folder to a user path.
    - If dest looks like a .zip file → zip the folder there
    - If dest looks like a .db file → zip next to it as <name>.zip, and also
      copy the consistent .db to that path
    - If dest is a directory → copy folder inside it
    - Otherwise treat as folder path to create
    """
    dest_path = os.path.abspath(str(dest_path).strip())
    lower = dest_path.lower()

    if lower.endswith(".zip"):
        parent = os.path.dirname(dest_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        zip_backup_folder(folder, dest_path)
        return dest_path

    if lower.endswith(".db"):
        parent = os.path.dirname(dest_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        # Save consistent .db for simple restore + zip of full set beside it
        main_db = _main_db_in_backup(folder)
        if main_db:
            shutil.copy2(main_db, dest_path)
        zip_path = os.path.splitext(dest_path)[0] + "_full.zip"
        zip_backup_folder(folder, zip_path)
        return dest_path

    # Directory: place folder copy inside / as that path
    if os.path.isdir(dest_path) or not os.path.splitext(dest_path)[1]:
        target = (
            os.path.join(dest_path, os.path.basename(folder))
            if os.path.isdir(dest_path)
            else dest_path
        )
        if os.path.exists(target):
            shutil.rmtree(target)
        shutil.copytree(folder, target)
        return target

    parent = os.path.dirname(dest_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if os.path.exists(dest_path):
        shutil.rmtree(dest_path)
    shutil.copytree(folder, dest_path)
    return dest_path


def _main_db_in_backup(folder_or_file: str) -> str | None:
    """Resolve path to the .db inside a backup folder (or the file itself)."""
    if os.path.isfile(folder_or_file) and folder_or_file.lower().endswith(".db"):
        return folder_or_file
    if not os.path.isdir(folder_or_file):
        return None
    dbs = [
        os.path.join(folder_or_file, name)
        for name in os.listdir(folder_or_file)
        if name.lower().endswith(".db") and not name.lower().endswith((".db-wal", ".db-shm"))
    ]
    # Prefer mbzc_erp.db
    for path in dbs:
        if os.path.basename(path).lower() == "mbzc_erp.db":
            return path
    return dbs[0] if dbs else None


def zip_backup_folder(folder: str, zip_path: str | None = None) -> str:
    """Zip all files in a backup folder. Returns zip path."""
    if not os.path.isdir(folder):
        raise FileNotFoundError(folder)
    if not zip_path:
        zip_path = folder.rstrip("\\/") + ".zip"
    parent = os.path.dirname(zip_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if os.path.isfile(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(os.listdir(folder)):
            full = os.path.join(folder, name)
            if os.path.isfile(full):
                zf.write(full, arcname=os.path.join(os.path.basename(folder), name))
    return zip_path


def resolve_backup_entry(name: str) -> str:
    """Return absolute path to a backup folder or legacy .db file in BACKUP_DIR."""
    name = os.path.basename(name)
    path = os.path.join(ensure_backup_dir(), name)
    if os.path.isdir(path) or os.path.isfile(path):
        return path
    raise FileNotFoundError(name)


def list_backup_files(limit: int | None = None) -> tuple[list[str], int]:
    """
    Return (names newest-first, total_count).
    Includes dated folders and legacy flat .db files.
    """
    backup_dir = ensure_backup_dir()
    entries: list[str] = []
    for name in os.listdir(backup_dir):
        path = os.path.join(backup_dir, name)
        if name.startswith("sqlite_backup_") and os.path.isdir(path):
            entries.append(name)
        elif name.endswith(".db") and os.path.isfile(path) and not name.endswith(
            (".db-wal", ".db-shm")
        ):
            # Legacy single-file backups (and stray zips' companions ignored)
            entries.append(name)
    entries = sorted(entries, reverse=True)
    total = len(entries)
    if limit is not None and limit > 0:
        entries = entries[:limit]
    return entries, total


def copy_backup_to_dest(backup_name: str, dest_path: str) -> str:
    """Copy an existing backup folder/file to a user-chosen path."""
    src = resolve_backup_entry(backup_name)
    if os.path.isdir(src):
        return _export_backup_folder(src, dest_path)
    # Legacy single .db
    dest_path = os.path.abspath(str(dest_path).strip())
    if not dest_path.lower().endswith(".db"):
        dest_path = dest_path + ".db"
    parent = os.path.dirname(dest_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    shutil.copy2(src, dest_path)
    return dest_path


def restore_sqlite(backup_path: str):
    """
    Replace the live DB from a backup folder or .db file.

    Uses online backup API into the live path, then clears leftover WAL/SHM.
    """
    if os.path.isdir(backup_path):
        src_db = _main_db_in_backup(backup_path)
        if not src_db:
            raise FileNotFoundError(f"No .db found in backup folder: {backup_path}")
    elif os.path.isfile(backup_path):
        src_db = backup_path
    else:
        raise FileNotFoundError(backup_path)

    dest = _sqlite_db_path()
    db.session.remove()
    db.engine.dispose()

    _sqlite_online_backup(src_db, dest)
    _remove_sidecars(dest)

    db.session.remove()
    db.engine.dispose()

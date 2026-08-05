"""Background jobs — MySQL sync + hourly SQLite backup (never on UI thread)."""

import logging
import os
import threading

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()
_backup_lock = threading.Lock()


def _should_skip_reloader(app) -> bool:
    """Avoid double-start under Flask debug reloader."""
    return bool(app.debug and os.environ.get("WERKZEUG_RUN_MAIN") == "false")


def init_scheduler(app):
    """
    Register background jobs independently:
    - auto_sync: push SQLite → MySQL when online (SYNC_AUTO_ENABLED)
    - auto_backup: copy SQLite every N hours (AUTO_BACKUP_ENABLED)

    Jobs never block sales / inventory requests.
    """
    if _should_skip_reloader(app):
        return

    sync_on = bool(app.config.get("SYNC_AUTO_ENABLED"))
    backup_on = bool(app.config.get("AUTO_BACKUP_ENABLED", True))
    drive_queue_on = bool(app.config.get("GOOGLE_DRIVE_AUTO_UPLOAD", True))

    if sync_on and not scheduler.get_job("auto_sync"):
        minutes = max(1, int(app.config.get("SYNC_INTERVAL_MINUTES", 15) or 15))

        def auto_sync():
            with app.app_context():
                try:
                    from app.services.sync_service import try_background_sync

                    log = try_background_sync(reason="cron")
                    if log is not None:
                        logger.info(
                            "Auto sync finished: %s — %s",
                            log.status,
                            (log.message or "")[:200],
                        )
                    else:
                        # Offline: still ensure queue exists as local bookkeeping
                        try:
                            from app.models import SyncQueue
                            from app.extensions import db

                            pending = SyncQueue.query.filter_by(is_resolved=False).count()
                            if pending:
                                logger.debug(
                                    "Auto sync deferred offline with %s queued change(s)",
                                    pending,
                                )
                        except Exception:
                            pass
                except Exception:
                    logger.exception("Auto sync job crashed")

        scheduler.add_job(
            auto_sync,
            "interval",
            minutes=minutes,
            id="auto_sync",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
            misfire_grace_time=60,
        )
        logger.info("Background MySQL sync every %s minute(s)", minutes)

    if backup_on and not scheduler.get_job("auto_backup"):
        hours = max(1, int(app.config.get("AUTO_BACKUP_INTERVAL_HOURS", 1) or 1))

        def auto_backup():
            # Skip if a backup is already running
            if not _backup_lock.acquire(blocking=False):
                logger.info("Auto backup skipped — already running")
                return
            try:
                with app.app_context():
                    from app.services.backup_service import backup_sqlite
                    from app.services.sync_service import is_local_sqlite

                    if not is_local_sqlite():
                        return
                    path = backup_sqlite()
                    logger.info("Auto backup created: %s", path)
                    if os.path.isdir(path):
                        try:
                            from app.services.toast_service import notify_backup_created

                            notify_backup_created(os.path.basename(path), auto=True)
                        except Exception:
                            pass
                    if os.path.isdir(path) and app.config.get("GOOGLE_DRIVE_AUTO_UPLOAD", True):
                        try:
                            from app.services import google_drive_service as drive

                            status = drive.upload_or_queue_folder(path)
                            logger.info("Auto backup Drive status: %s (%s)", status, path)
                        except Exception:
                            logger.exception("Google Drive upload/queue after auto backup failed")
            except Exception:
                logger.exception("Auto backup job crashed")
            finally:
                _backup_lock.release()

        scheduler.add_job(
            auto_backup,
            "interval",
            hours=hours,
            id="auto_backup",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
            misfire_grace_time=300,
        )
        logger.info("Background SQLite backup every %s hour(s)", hours)

    if drive_queue_on and not scheduler.get_job("drive_upload_queue"):
        minutes = max(1, int(app.config.get("GOOGLE_DRIVE_QUEUE_INTERVAL_MINUTES", 5) or 5))

        def drive_upload_queue():
            with app.app_context():
                try:
                    from app.services import google_drive_service as drive

                    if not drive.is_connected() or not drive.list_upload_queue():
                        return
                    result = drive.process_upload_queue(manual=False)
                    if result.get("uploaded"):
                        logger.info("Drive queue upload: %s", result.get("message"))
                except Exception:
                    logger.exception("Drive upload queue job crashed")

        scheduler.add_job(
            drive_upload_queue,
            "interval",
            minutes=minutes,
            id="drive_upload_queue",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
            misfire_grace_time=120,
        )
        logger.info("Google Drive upload queue check every %s minute(s)", minutes)

    if (sync_on or backup_on or drive_queue_on) and not scheduler.running:
        scheduler.start()

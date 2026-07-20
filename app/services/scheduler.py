from apscheduler.schedulers.background import BackgroundScheduler

scheduler = BackgroundScheduler()


def init_scheduler(app):
    from app.services.sync_service import is_mysql_available, run_sync

    def auto_sync():
        with app.app_context():
            from app.services.sync_service import is_local_sqlite

            if is_local_sqlite() and is_mysql_available():
                run_sync()

    if not scheduler.running:
        minutes = app.config.get("SYNC_INTERVAL_MINUTES", 15)
        scheduler.add_job(auto_sync, "interval", minutes=minutes, id="auto_sync")
        scheduler.start()

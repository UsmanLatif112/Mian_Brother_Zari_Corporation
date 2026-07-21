import logging
import os

from flask import Flask, render_template, send_from_directory

from config import apply_database_uri, get_config
from app.extensions import csrf, db, login_manager, migrate


def _normalize_sqlite_uri(app: Flask) -> None:
    uri = app.config.get("SQLALCHEMY_DATABASE_URI") or ""
    if not uri.startswith("sqlite:///") or ":memory:" in uri:
        return
    name = os.path.basename(uri.replace("sqlite:///", ""))
    candidates = [
        os.path.join(app.instance_path, name),
        os.path.join(os.path.dirname(app.root_path), name),
    ]
    for path in candidates:
        if os.path.isfile(path):
            app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + path.replace("\\", "/")
            return
    default = os.path.join(app.instance_path, name)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + default.replace("\\", "/")


def _apply_desktop_paths(app: Flask) -> None:
    """Writable DB / uploads / backups outside the frozen bundle."""
    if os.environ.get("DESKTOP_APP", "").lower() not in ("1", "true", "yes"):
        return
    from app.runtime_paths import backups_dir, instance_dir, uploads_dir

    app.instance_path = instance_dir()
    app.config["UPLOAD_FOLDER"] = uploads_dir()
    app.config["BACKUP_DIR"] = backups_dir()
    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(app.config["BACKUP_DIR"], exist_ok=True)

    # Serve user uploads at /static/uploads/... even when folder is outside bundle static
    upload_root = app.config["UPLOAD_FOLDER"]

    def _serve_uploads(filename: str):
        return send_from_directory(upload_root, filename)

    # Replace default static handler so uploads resolve from writable data dir
    static_folder = app.static_folder

    def _desktop_static(filename: str):
        if filename.startswith("uploads/") or filename.startswith("uploads\\"):
            rel = filename.split("/", 1)[-1].split("\\", 1)[-1]
            return send_from_directory(upload_root, rel)
        return send_from_directory(static_folder, filename)

    app.view_functions["static"] = _desktop_static
    # Keep explicit route as backup
    app.add_url_rule(
        "/static/uploads/<path:filename>",
        endpoint="desktop_uploads",
        view_func=_serve_uploads,
    )


def create_app(config_class=None):
    desktop = os.environ.get("DESKTOP_APP", "").lower() in ("1", "true", "yes")
    if desktop:
        from app.runtime_paths import instance_dir, static_dir, templates_dir

        app = Flask(
            __name__,
            instance_path=instance_dir(),
            instance_relative_config=True,
            template_folder=templates_dir(),
            static_folder=static_dir(),
        )
    else:
        app = Flask(__name__, instance_relative_config=True)

    cfg = config_class or get_config()
    app.config.from_object(cfg)
    apply_database_uri(app)
    _apply_desktop_paths(app)

    os.makedirs(app.instance_path, exist_ok=True)
    backup_dir = app.config["BACKUP_DIR"]
    if not os.path.isabs(backup_dir):
        from config import basedir

        backup_dir = os.path.join(basedir, backup_dir)
        app.config["BACKUP_DIR"] = backup_dir
    os.makedirs(backup_dir, exist_ok=True)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    _normalize_sqlite_uri(app)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)

    from app.models import User

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    register_blueprints(app)
    register_error_handlers(app)
    register_context_processors(app)

    with app.app_context():
        try:
            from app.services.dashboard_service import ensure_customer_type_column

            ensure_customer_type_column()
        except Exception:
            logging.getLogger(__name__).warning("Could not ensure customer_type column")

    if app.config.get("SYNC_AUTO_ENABLED"):
        try:
            from app.services.scheduler import init_scheduler

            init_scheduler(app)
        except Exception:
            logging.getLogger(__name__).warning("Scheduler not started")

    @app.cli.command("init-db")
    def init_db():
        from app.utils.seed import seed_database

        db.create_all()
        seed_database()
        print("Database initialized.")

    @app.cli.command("reset-db")
    def reset_db():
        """Drop all tables, recreate schema, and seed defaults (fresh data for testing)."""
        from app.services.dashboard_service import ensure_customer_type_column
        from app.utils.seed import seed_database

        db.drop_all()
        db.create_all()
        ensure_customer_type_column()
        seed_database()
        print("Database reset: all tables truncated and defaults seeded (admin / admin123).")

    return app


def register_blueprints(app):
    from app.auth.routes import auth_bp
    from app.dashboard.routes import dashboard_bp
    from app.inventory.routes import inventory_bp
    from app.sales.routes import sales_bp
    from app.purchases.routes import purchases_bp
    from app.expenses.routes import expenses_bp
    from app.customers.routes import customers_bp
    from app.vendors.routes import vendors_bp
    from app.maintenance.routes import maintenance_bp
    from app.journal.routes import journal_bp
    from app.api.routes import api_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(inventory_bp, url_prefix="/inventory")
    app.register_blueprint(sales_bp, url_prefix="/sales")
    app.register_blueprint(purchases_bp, url_prefix="/purchases")
    app.register_blueprint(expenses_bp, url_prefix="/expenses")
    app.register_blueprint(customers_bp, url_prefix="/customers")
    app.register_blueprint(vendors_bp, url_prefix="/vendors")
    app.register_blueprint(maintenance_bp, url_prefix="/sync-backup")
    app.register_blueprint(journal_bp, url_prefix="/journal")
    app.register_blueprint(api_bp, url_prefix="/api")


def register_error_handlers(app):
    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("errors/403.html"), 403

    @app.errorhandler(500)
    def server_error(e):
        return render_template("errors/500.html"), 500


def register_context_processors(app):
    from app.services.settings_service import get_business_info

    @app.context_processor
    def inject_globals():
        from flask import request

        from app.services.sync_service import get_sync_target_label, is_local_sqlite
        from app.utils.search_context import resolve_search_context

        search_ctx = resolve_search_context(request.path)

        return {
            "business": get_business_info(),
            "app_name": "MBF ERP",
            "brand_short": "MBF",
            "offline_sqlite": is_local_sqlite(),
            "sync_target_label": get_sync_target_label(),
            "search_scope": search_ctx.scope,
            "search_table_selector": search_ctx.table_selector,
            "search_placeholder": search_ctx.placeholder,
            "search_mode": search_ctx.mode,
        }

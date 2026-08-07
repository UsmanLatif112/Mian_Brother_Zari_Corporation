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


def _configure_sqlite_engine(app: Flask) -> None:
    """Timeout + WAL so desktop Waitress threads don't stall on locks."""
    uri = app.config.get("SQLALCHEMY_DATABASE_URI") or ""
    if not uri.startswith("sqlite"):
        return

    options = dict(app.config.get("SQLALCHEMY_ENGINE_OPTIONS") or {})
    connect_args = dict(options.get("connect_args") or {})
    connect_args.setdefault("timeout", 30)
    connect_args.setdefault("check_same_thread", False)
    options["connect_args"] = connect_args
    options.setdefault("pool_pre_ping", True)
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = options


def _enable_sqlite_wal(app: Flask) -> None:
    uri = app.config.get("SQLALCHEMY_DATABASE_URI") or ""
    if not uri.startswith("sqlite"):
        return

    from sqlalchemy import event

    with app.app_context():
        engine = db.engine

        @event.listens_for(engine, "connect")
        def _sqlite_on_connect(dbapi_conn, connection_record):  # noqa: ARG001
            cursor = dbapi_conn.cursor()
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.execute("PRAGMA synchronous=NORMAL")
            finally:
                cursor.close()


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
    _configure_sqlite_engine(app)

    db.init_app(app)
    _enable_sqlite_wal(app)
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
    register_registration_guard(app)

    with app.app_context():
        try:
            from app.services.dashboard_service import ensure_customer_type_column

            ensure_customer_type_column()
        except Exception:
            logging.getLogger(__name__).warning("Could not ensure customer_type column")
        try:
            from app.services.user_registry_service import ensure_user_registration_columns

            ensure_user_registration_columns()
        except Exception:
            logging.getLogger(__name__).warning("Could not ensure user registration columns")
        try:
            from app.models.account import AccountAmountTaken, AccountCashSetup

            AccountCashSetup.__table__.create(db.engine, checkfirst=True)
            AccountAmountTaken.__table__.create(db.engine, checkfirst=True)
        except Exception:
            logging.getLogger(__name__).warning("Could not ensure account tables", exc_info=True)

    if app.config.get("SYNC_AUTO_ENABLED") or app.config.get("AUTO_BACKUP_ENABLED", True):
        try:
            from app.services.scheduler import init_scheduler

            init_scheduler(app)
            if app.config.get("SYNC_AUTO_ENABLED"):
                logging.getLogger(__name__).info(
                    "Background MySQL sync enabled (every %s min when online)",
                    app.config.get("SYNC_INTERVAL_MINUTES", 15),
                )
            if app.config.get("AUTO_BACKUP_ENABLED", True):
                logging.getLogger(__name__).info(
                    "Background SQLite backup enabled (every %s hour(s))",
                    app.config.get("AUTO_BACKUP_INTERVAL_HOURS", 1),
                )
        except Exception:
            logging.getLogger(__name__).warning("Scheduler not started", exc_info=True)
    else:
        logging.getLogger(__name__).info(
            "Background jobs off (SYNC_AUTO_ENABLED / AUTO_BACKUP_ENABLED)"
        )

    @app.cli.command("init-db")
    def init_db():
        from app.utils.seed import seed_database

        db.create_all()
        seed_database(create_default_admin=True)
        print("Database initialized.")

    @app.cli.command("reset-db")
    def reset_db():
        """Drop all tables, recreate schema, and seed defaults (fresh data for testing)."""
        from app.services.dashboard_service import ensure_customer_type_column
        from app.utils.seed import seed_database

        db.drop_all()
        db.create_all()
        ensure_customer_type_column()
        seed_database(create_default_admin=True)
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
    from app.salesmen.routes import salesmen_bp
    from app.categories.routes import categories_bp
    from app.account.routes import account_bp
    from app.maintenance.routes import maintenance_bp
    from app.journal.routes import journal_bp
    from app.api.routes import api_bp

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(inventory_bp, url_prefix="/inventory")
    app.register_blueprint(sales_bp, url_prefix="/sales")
    app.register_blueprint(purchases_bp, url_prefix="/purchases")
    app.register_blueprint(expenses_bp, url_prefix="/expenses")
    app.register_blueprint(customers_bp, url_prefix="/customers")
    app.register_blueprint(vendors_bp, url_prefix="/vendors")
    app.register_blueprint(salesmen_bp, url_prefix="/salesmen")
    app.register_blueprint(categories_bp, url_prefix="/categories")
    app.register_blueprint(account_bp, url_prefix="/account")
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


def register_registration_guard(app):
    from flask import flash, jsonify, redirect, request, url_for
    from flask_login import current_user

    from app.utils.decorators import REGISTRATION_EXEMPT_ENDPOINTS

    @app.before_request
    def enforce_user_registration():
        if not current_user.is_authenticated:
            return None
        if current_user.is_registration_complete():
            return None
        ep = request.endpoint
        if not ep or ep in REGISTRATION_EXEMPT_ENDPOINTS:
            return None
        if ep == "static" or (request.path or "").startswith("/static"):
            return None
        if request.path.startswith("/api/") and ep in ("api.internet_status", "api.poll_toasts"):
            return None
        wants_json = (
            request.is_json
            or request.accept_mimetypes.best == "application/json"
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        )
        if wants_json or (ep and ep.startswith("api.")):
            return jsonify(
                {
                    "ok": False,
                    "error": "Register this computer with your activation key first.",
                    "needs_registration": True,
                }
            ), 403
        flash("Register this computer with your activation key to access this section.", "warning")
        return redirect(url_for("dashboard.index", registration_required=1))


def register_context_processors(app):
    from app.services.settings_service import get_business_info
    from app.utils.weight_utils import (
        clean_number,
        format_movement_qty_display,
        format_qty_display,
        format_stock_display,
        format_stock_total_display,
    )

    @app.template_filter("qty_display")
    def qty_display_filter(qty, product=None, unit_weight=None, weight_unit=None):
        """Format stock qty as whole sealed bags + open weight when applicable."""
        from decimal import Decimal

        # StockMovement: prefer actual open-sale kg (avoids -9.99 from 0.333 bags)
        if product is not None and hasattr(qty, "movement_type") and hasattr(qty, "quantity"):
            return format_movement_qty_display(qty, product)

        # StockLayer passed as second arg (detail batches table)
        if product is not None and hasattr(product, "open_weight_remaining"):
            layer = product
            sealed = Decimal(str(getattr(layer, "quantity_remaining", None) or qty or 0))
            open_w = Decimal(str(getattr(layer, "open_weight_remaining", None) or 0))
            uw = getattr(layer, "effective_unit_weight", None)
            wu = getattr(layer, "effective_weight_unit", None) or "kg"
            if uw and open_w > 0:
                if sealed > 0:
                    return f"{clean_number(sealed)} + {clean_number(open_w)} {wu}"
                return f"{clean_number(open_w)} {wu}"
            if uw:
                return format_qty_display(sealed, uw, wu)
            return format_qty_display(sealed, None, wu)
        if unit_weight is not None:
            return format_qty_display(qty, unit_weight, weight_unit or "kg")
        if product is not None:
            return format_qty_display(
                qty,
                getattr(product, "unit_weight", None),
                getattr(product, "weight_unit", None) or "kg",
            )
        return format_qty_display(qty, unit_weight, weight_unit or "kg")

    @app.template_filter("movement_qty_display")
    def movement_qty_display_filter(movement, product=None):
        return format_movement_qty_display(movement, product)

    @app.template_filter("stock_display")
    def stock_display_filter(product):
        return format_stock_display(product)

    @app.template_filter("stock_total_display")
    def stock_total_display_filter(product):
        return format_stock_total_display(product)

    @app.template_filter("clean_num")
    def clean_num_filter(value):
        """50.000 → 50, 12.500 → 12.5"""
        return clean_number(value)

    @app.context_processor
    def inject_globals():
        from datetime import date

        from flask import request
        from flask_login import current_user

        from app.services.sync_service import get_sync_target_label, is_local_sqlite
        from app.services.update_service import current_version_info, is_desktop_app, updates_enabled
        from app.utils.search_context import resolve_search_context
        from app.utils.working_date import (
            can_manage_working_date,
            get_working_date,
            is_custom_working_date,
        )

        search_ctx = resolve_search_context(request.path)
        working = get_working_date()
        custom_wd = is_custom_working_date()
        ver = current_version_info()

        from flask import url_for

        suite_name = "Agri Books"
        suite_logo = url_for("static", filename="img/logo.png")
        brand_name = "Agri"
        brand_sub = "Books"
        brand_logo = suite_logo
        brand_is_suite = True
        display_company = None

        business = get_business_info()
        if current_user.is_authenticated:
            if current_user.is_super_admin():
                brand_name, brand_sub, brand_logo = "Agri", "Books", suite_logo
                brand_is_suite = True
            else:
                display_company = (getattr(current_user, "company_name", None) or "").strip() or None
                brand_name = display_company or "Business"
                brand_sub = ""
                # Never fall back to product logo for staff — company branding only
                brand_logo = getattr(current_user, "company_logo_url", None)
                brand_is_suite = False
                if display_company:
                    business = {**business, "company_name": display_company}

        ctx = {
            "business": business,
            "app_name": suite_name,
            "brand_short": "ZS",
            "suite_name": suite_name,
            "suite_logo": suite_logo,
            "brand_name": brand_name,
            "brand_sub": brand_sub,
            "brand_logo": brand_logo,
            "brand_is_suite": brand_is_suite,
            "developer_name": "U. Technologies",
            "developer_url": "https://udottechnologies.com/",
            "offline_sqlite": is_local_sqlite(),
            "sync_target_label": get_sync_target_label(),
            "search_scope": search_ctx.scope,
            "search_table_selector": search_ctx.table_selector,
            "search_placeholder": search_ctx.placeholder,
            "search_mode": search_ctx.mode,
            "working_date": working.isoformat(),
            "working_date_display": working.strftime("%d %b %Y"),
            "calendar_today": date.today().isoformat(),
            "is_custom_working_date": custom_wd,
            "is_desktop_app": is_desktop_app(),
            "app_version": ver["version"],
            "app_build": ver["build"],
            "updates_enabled": updates_enabled(),
        }
        if current_user.is_authenticated:
            ctx["can_manage_working_date"] = can_manage_working_date(current_user)
        else:
            ctx["can_manage_working_date"] = False
        return ctx

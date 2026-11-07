from pathlib import Path

from flask import Flask, redirect, send_file, url_for
from flask_login import current_user

from portal.config import Config
from portal.extensions import csrf, db, login_manager


def create_app(config_object: type = Config) -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
        instance_relative_config=True,
    )
    app.config.from_object(config_object)
    for key in ("UPLOAD_FOLDER", "RELEASES_FOLDER", "SQLITE_PACKAGES_FOLDER"):
        Path(app.config[key]).mkdir(parents=True, exist_ok=True)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    from portal.models import PortalUser

    @login_manager.user_loader
    def load_user(user_id: str):
        try:
            return db.session.get(PortalUser, int(user_id))
        except (TypeError, ValueError):
            return None

    from portal.auth.routes import auth_bp
    from portal.admin.routes import admin_bp
    from portal.company.routes import company_bp
    from portal.downloads.routes import downloads_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(company_bp, url_prefix="/app")
    app.register_blueprint(downloads_bp, url_prefix="/downloads")

    @app.context_processor
    def inject_branding():
        suite_name = app.config.get("SUITE_NAME", "Agri Books")
        suite_logo = url_for("static", filename="img/logo.png")
        brand_name = "Agri"
        brand_sub = "Books"
        brand_logo = suite_logo
        brand_is_suite = True

        if current_user.is_authenticated and not current_user.is_super_admin():
            company = getattr(current_user, "company", None)
            display = (company.name if company else "") or ""
            brand_name = display.strip() or "Business"
            brand_sub = ""
            brand_is_suite = False
            brand_logo = None
            if company and company.id:
                if company.logo_data or company.logo_path:
                    brand_logo = url_for("company_logo", company_id=company.id)

        return {
            "app_name": suite_name,
            "suite_name": suite_name,
            "suite_logo": suite_logo,
            "brand_name": brand_name,
            "brand_sub": brand_sub,
            "brand_logo": brand_logo,
            "brand_is_suite": brand_is_suite,
            "developer_name": app.config.get("DEVELOPER_NAME", "U. Technologies"),
            "developer_url": app.config.get("DEVELOPER_URL", "https://udottechnologies.com/"),
            "portal_domain": app.config.get("PORTAL_DOMAIN"),
            "update_manifest_url": app.config.get("UPDATE_MANIFEST_URL"),
        }

    @app.route("/media/company-logo/<int:company_id>")
    def company_logo(company_id: int):
        from portal.models import Company

        company = db.session.get(Company, company_id)
        if not company or not company.logo_data:
            return redirect(url_for("static", filename="img/logo.png"))
        from io import BytesIO

        ext = (Path(company.logo_path or "logo.png").suffix or ".png").lstrip(".")
        mime = {
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "png": "image/png",
            "webp": "image/webp",
            "gif": "image/gif",
        }.get(ext.lower(), "image/png")
        return send_file(BytesIO(company.logo_data), mimetype=mime)

    # Public update channel (desktop apps + CDN-style path on agribooks domain)
    @app.route("/updates/version.json")
    def updates_manifest():
        path = Path(app.config["RELEASES_FOLDER"]) / "version.json"
        if not path.is_file():
            return {
                "name": "AgriBooks",
                "version": "0.0.0",
                "build": 0,
                "url": "",
                "notes": "No release uploaded yet.",
            }
        return send_file(path, mimetype="application/json")

    @app.route("/updates/<path:filename>")
    def updates_file(filename: str):
        path = Path(app.config["RELEASES_FOLDER"]) / filename
        if not path.is_file() or ".." in filename:
            return ("Not found", 404)
        return send_file(path, as_attachment=True, download_name=path.name)

    @app.route("/")
    def index():
        if current_user.is_authenticated:
            if current_user.is_super_admin():
                return redirect(url_for("admin.dashboard"))
            return redirect(url_for("company.dashboard"))
        return redirect(url_for("auth.login"))

    with app.app_context():
        try:
            db.create_all()
        except Exception as exc:  # pragma: no cover
            app.logger.warning("Could not create tables yet: %s", exc)

    return app

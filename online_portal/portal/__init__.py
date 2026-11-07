from pathlib import Path

from flask import Flask, redirect, request, send_file, url_for
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
        suite_favicon = url_for("static", filename="img/favicon-32.png")
        brand_name = "Agri"
        brand_sub = "Books"
        brand_logo = suite_logo
        brand_favicon = suite_favicon
        brand_is_suite = True

        if current_user.is_authenticated and not current_user.is_super_admin():
            company = getattr(current_user, "company", None)
            display = (company.name if company else "") or ""
            brand_name = display.strip() or "Business"
            brand_sub = ""
            brand_is_suite = False
            brand_logo = None
            brand_favicon = suite_favicon
            # Logos are managed only by Super Admin on the portal (no desktop/registry hydrate)
            if company and company.id and (company.logo_data or company.logo_path):
                brand_logo = url_for("company_logo", company_id=company.id)
                brand_favicon = brand_logo

        return {
            "app_name": suite_name,
            "suite_name": suite_name,
            "suite_logo": suite_logo,
            "brand_name": brand_name,
            "brand_sub": brand_sub,
            "brand_logo": brand_logo,
            "brand_favicon": brand_favicon,
            "brand_is_suite": brand_is_suite,
            "developer_name": app.config.get("DEVELOPER_NAME", "U. Technologies"),
            "developer_url": app.config.get("DEVELOPER_URL", "https://udottechnologies.com/"),
            "portal_domain": app.config.get("PORTAL_DOMAIN"),
            "update_manifest_url": app.config.get("UPDATE_MANIFEST_URL"),
        }

    @app.route("/media/company-logo/<int:company_id>")
    def company_logo(company_id: int):
        from flask import Response

        from portal.models import Company

        def _mime_for(data: bytes, logo_path: str | None) -> str | None:
            if data.startswith(b"\xff\xd8\xff"):
                return "image/jpeg"
            if data.startswith(b"\x89PNG\r\n\x1a\n"):
                return "image/png"
            if data.startswith((b"GIF87a", b"GIF89a")):
                return "image/gif"
            if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
                return "image/webp"
            ext = (Path(logo_path or "").suffix or "").lstrip(".").lower()
            return {
                "jpg": "image/jpeg",
                "jpeg": "image/jpeg",
                "png": "image/png",
                "webp": "image/webp",
                "gif": "image/gif",
            }.get(ext)

        def _is_truncated_jpeg(data: bytes) -> bool:
            # Old MySQL BLOB capped at 64KiB-1; truncated JPEGs usually lack EOI marker
            if not data.startswith(b"\xff\xd8\xff"):
                return False
            if len(data) >= 65535 and not data.rstrip(b"\x00").endswith(b"\xff\xd9"):
                return True
            return False

        try:
            company = db.session.get(Company, company_id)
            logo_path = getattr(company, "logo_path", None) if company else None
            disk = None
            if company and logo_path:
                candidate = Path(app.config["UPLOAD_FOLDER"]) / logo_path
                if candidate.is_file():
                    disk = candidate

            blob = bytes(company.logo_data) if company and company.logo_data else None
            data = None
            # Prefer full disk file over a truncated DB blob
            if disk is not None:
                disk_bytes = disk.read_bytes()
                if disk_bytes and not _is_truncated_jpeg(disk_bytes):
                    data = disk_bytes
                    if not blob or len(disk_bytes) > len(blob) or _is_truncated_jpeg(blob):
                        try:
                            company.logo_data = data
                            db.session.commit()
                        except Exception:
                            db.session.rollback()
            if data is None and blob and not _is_truncated_jpeg(blob):
                data = blob

            if not data:
                if request.args.get("strict"):
                    return ("", 404)
                return redirect(url_for("static", filename="img/logo.png"))

            mime = _mime_for(data, logo_path) or "application/octet-stream"
            if not mime.startswith("image/"):
                if request.args.get("strict"):
                    return ("", 404)
                return redirect(url_for("static", filename="img/logo.png"))

            return Response(
                data,
                mimetype=mime,
                headers={
                    "Cache-Control": "no-cache, max-age=0, must-revalidate",
                    "Pragma": "no-cache",
                    "Content-Length": str(len(data)),
                },
            )
        except Exception:
            app.logger.exception("company_logo failed for id=%s", company_id)
            if request.args.get("strict"):
                return ("", 404)
            return redirect(url_for("static", filename="img/logo.png"))

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

    # Do NOT call db.create_all() here. LiteSpeed/Passenger starts many workers;
    # each create_all() hits MySQL and can lock/suspend the cPanel account.
    # Run schema once:  python scripts/migrate_schema.py

    return app

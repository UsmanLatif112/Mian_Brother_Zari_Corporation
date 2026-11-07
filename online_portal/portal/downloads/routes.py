import json
import shutil
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from portal.extensions import db
from portal.models import Company
from portal.services.install_package import build_company_install_zip

downloads_bp = Blueprint("downloads", __name__)


def super_admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_super_admin():
            flash("Super Admin access only.", "danger")
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)

    return wrapped


def _releases_dir() -> Path:
    return Path(current_app.config["RELEASES_FOLDER"])


def _sqlite_dir() -> Path:
    return Path(current_app.config["SQLITE_PACKAGES_FOLDER"])


def _packages_cache() -> Path:
    path = Path(current_app.config["UPLOAD_FOLDER"]) / "install_packages"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_manifest() -> dict:
    path = _releases_dir() / "version.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


@downloads_bp.route("/")
@login_required
def index():
    """Company users + Super Admin: download desktop app / company SQLite."""
    if current_user.is_shop_clerk():
        flash("Shop clerks use the offline app only.", "warning")
        return redirect(url_for("auth.logout"))
    manifest = _read_manifest()
    company = None if current_user.is_super_admin() else current_user.company
    sqlite_path = None
    if company:
        candidate = _sqlite_dir() / str(company.id) / "mbzc_erp.db"
        if candidate.is_file():
            sqlite_path = candidate
    return render_template(
        "downloads/index.html",
        manifest=manifest,
        company=company,
        has_sqlite=bool(sqlite_path),
        registration_key=None
        if current_user.is_super_admin()
        else current_user.registration_key,
    )


@downloads_bp.route("/app")
@login_required
def download_app():
    """
    Company Admin: personalized zip = app + SQLite restricted to their
    company users/keys (and business data if Super Admin uploaded it).
    Super Admin: raw published release zip.
    """
    manifest = _read_manifest()
    if current_user.is_super_admin():
        filename = (manifest.get("filename") or "").strip()
        if not filename:
            flash("No desktop app release uploaded yet.", "warning")
            return redirect(url_for("downloads.index"))
        path = _releases_dir() / filename
        if not path.is_file():
            flash("Release file missing on server.", "danger")
            return redirect(url_for("downloads.index"))
        return send_file(path, as_attachment=True, download_name=path.name)

    company = current_user.company
    if not company:
        flash("No company linked to this account.", "danger")
        return redirect(url_for("downloads.index"))
    if not company.is_active or not current_user.is_active_user:
        flash("Account or company is inactive.", "danger")
        return redirect(url_for("downloads.index"))

    try:
        zip_path = build_company_install_zip(
            company=company,
            portal_user=current_user,
            releases_dir=_releases_dir(),
            sqlite_packages_dir=_sqlite_dir(),
            cache_dir=_packages_cache(),
            manifest=manifest,
        )
    except ValueError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("downloads.index"))
    except Exception:
        current_app.logger.exception("Failed building install package")
        flash("Could not build install package. Try again or contact support.", "danger")
        return redirect(url_for("downloads.index"))

    return send_file(zip_path, as_attachment=True, download_name=zip_path.name)


@downloads_bp.route("/sqlite/<int:company_id>")
@login_required
def download_sqlite(company_id: int):
    if not current_user.is_super_admin():
        if not current_user.company_id or current_user.company_id != company_id:
            flash("Not allowed.", "danger")
            return redirect(url_for("downloads.index"))
    path = _sqlite_dir() / str(company_id) / "mbzc_erp.db"
    if not path.is_file():
        flash("No SQLite package for this company yet.", "warning")
        return redirect(request.referrer or url_for("downloads.index"))
    company = db.session.get(Company, company_id)
    name = secure_filename((company.name if company else "company") + "_mbzc_erp.db")
    return send_file(path, as_attachment=True, download_name=name)


@downloads_bp.route("/admin/releases", methods=["GET", "POST"])
@super_admin_required
def admin_releases():
    """Upload Agri Books desktop zip + version.json for agribooks domain updates."""
    if request.method == "POST":
        version = (request.form.get("version") or "").strip()
        build = (request.form.get("build") or "").strip() or "0"
        notes = (request.form.get("notes") or "").strip()
        file = request.files.get("package")
        if not version or not file or not file.filename:
            flash("Version and package zip are required.", "danger")
            return redirect(url_for("downloads.admin_releases"))
        if not secure_filename(file.filename).lower().endswith(".zip"):
            flash("Upload a .zip package.", "danger")
            return redirect(url_for("downloads.admin_releases"))
        dest_name = f"AgriBooks-{version}.zip"
        dest = _releases_dir() / dest_name
        file.save(dest)
        domain = current_app.config.get("PORTAL_DOMAIN", "agribooks.udottechnologies.com")
        public_url = f"https://{domain}/updates/{dest_name}"
        try:
            build_n = int(build)
        except ValueError:
            build_n = 0
        manifest = {
            "name": "AgriBooks",
            "version": version,
            "build": build_n,
            "filename": dest_name,
            "url": public_url,
            "notes": notes or f"Agri Books {version}",
            "published_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        }
        (_releases_dir() / "version.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        # Clear cached personalized packages so next download uses new release
        cache = _packages_cache()
        for old in cache.glob("AgriBooks-*.zip"):
            try:
                old.unlink()
            except OSError:
                pass
        flash(f"Release {version} published. Manifest: /updates/version.json", "success")
        return redirect(url_for("downloads.admin_releases"))

    return render_template(
        "downloads/admin_releases.html",
        manifest=_read_manifest(),
        files=sorted(_releases_dir().glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True),
    )


@downloads_bp.route("/admin/sqlite", methods=["GET", "POST"])
@super_admin_required
def admin_sqlite():
    """Upload SQLite DB package per company (business data for that shop only)."""
    companies = Company.query.order_by(Company.name).all()
    if request.method == "POST":
        company_id = request.form.get("company_id", type=int)
        file = request.files.get("sqlite_file")
        company = db.session.get(Company, company_id) if company_id else None
        if not company or not file or not file.filename:
            flash("Company and .db file required.", "danger")
            return redirect(url_for("downloads.admin_sqlite"))
        folder = _sqlite_dir() / str(company.id)
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / "mbzc_erp.db"
        file.save(dest)
        named = folder / f"{secure_filename(company.name)}.db"
        shutil.copy2(dest, named)
        # Invalidate install caches for this company
        for old in _packages_cache().glob(f"AgriBooks-*{secure_filename(company.name)}*"):
            try:
                old.unlink()
            except OSError:
                pass
        flash(
            f"SQLite saved for {company.name}. "
            "Company downloads will include this data, restricted to their portal users/keys.",
            "success",
        )
        return redirect(url_for("downloads.admin_sqlite"))

    packages = []
    for c in companies:
        p = _sqlite_dir() / str(c.id) / "mbzc_erp.db"
        packages.append(
            {
                "company": c,
                "exists": p.is_file(),
                "size": p.stat().st_size if p.is_file() else 0,
                "mtime": datetime.fromtimestamp(p.stat().st_mtime) if p.is_file() else None,
            }
        )
    return render_template("downloads/admin_sqlite.html", packages=packages)

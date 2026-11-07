import secrets
from functools import wraps
from pathlib import Path

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy.orm.attributes import flag_modified
from werkzeug.utils import secure_filename

from portal.admin.forms import CompanyForm, CompanyUserForm
from portal.extensions import db
from portal.models import Company, PortalRole, PortalUser
from portal.services.registry_mirror import mirror_user_to_registry

admin_bp = Blueprint("admin", __name__)

_ALLOWED_LOGO_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def super_admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_super_admin():
            flash("Super Admin access only.", "danger")
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)

    return wrapped


def _new_sync_code() -> str:
    return secrets.token_hex(4).upper()


def _flash_form_errors(form) -> None:
    for field_name, errs in form.errors.items():
        label = getattr(getattr(form, field_name, None), "label", None)
        title = label.text if label is not None else field_name
        for err in errs:
            flash(f"{title}: {err}", "danger")


def _save_logo(file_storage, company: Company) -> bool:
    """
    Persist uploaded logo to disk + company.logo_data.
    Returns True if a new logo was saved.
    """
    if not file_storage:
        return False
    filename = (getattr(file_storage, "filename", None) or "").strip()
    if not filename:
        return False

    safe = secure_filename(filename) or "logo.png"
    ext = Path(safe).suffix.lower()
    if ext not in _ALLOWED_LOGO_EXT:
        flash("Logo must be JPG, PNG, WEBP, or GIF.", "danger")
        return False

    # Prefer stream from request; FileStorage may already be partially read
    try:
        if hasattr(file_storage, "stream") and hasattr(file_storage.stream, "seek"):
            file_storage.stream.seek(0)
        data = file_storage.read()
    except Exception as exc:
        flash(f"Could not read logo file: {exc}", "danger")
        return False

    if not data:
        flash("Logo file was empty.", "danger")
        return False

    # Soft cap (portal branding) — keep under a few MB
    if len(data) > 8 * 1024 * 1024:
        flash("Logo is too large (max 8 MB).", "danger")
        return False

    upload_root = Path(current_app.config["UPLOAD_FOLDER"]) / "logos"
    try:
        upload_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        flash(f"Could not create upload folder: {exc}", "danger")
        return False

    name = f"company_{company.id}{ext}"
    path = upload_root / name
    try:
        path.write_bytes(data)
    except OSError as exc:
        flash(f"Could not write logo file: {exc}", "danger")
        return False

    company.logo_path = f"logos/{name}"
    company.logo_data = data
    flag_modified(company, "logo_data")
    # Helpful when MySQL still has old 64KB BLOB (should be LONGBLOB after migrate)
    if len(data) > 65000:
        current_app.logger.info(
            "Saving company %s logo (%s bytes) to %s", company.id, len(data), path
        )
    return True


@admin_bp.route("/")
@super_admin_required
def dashboard():
    companies = Company.query.order_by(Company.name).all()
    users = (
        PortalUser.query.filter(PortalUser.role != PortalRole.SUPER_ADMIN.value)
        .order_by(PortalUser.full_name)
        .all()
    )
    return render_template(
        "admin/dashboard.html",
        companies=companies,
        users=users,
        company_count=len(companies),
        active_companies=sum(1 for c in companies if c.is_active),
        user_count=len(users),
    )


@admin_bp.route("/companies")
@super_admin_required
def companies():
    rows = Company.query.order_by(Company.name).all()
    return render_template("admin/companies.html", companies=rows)


@admin_bp.route("/companies/new", methods=["GET", "POST"])
@super_admin_required
def company_new():
    form = CompanyForm()
    if form.validate_on_submit():
        company = Company(
            name=form.name.data.strip(),
            sync_code=_new_sync_code(),
            notes=(form.notes.data or "").strip() or None,
            is_active=bool(form.is_active.data),
        )
        db.session.add(company)
        db.session.flush()
        logo_file = request.files.get("logo") or form.logo.data
        saved = _save_logo(logo_file, company)
        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            flash(
                f"Company created but logo failed to store (logo column may be too small). "
                f"Run migrate_schema.py. ({exc})",
                "danger",
            )
            return redirect(url_for("admin.company_detail", company_id=company.id))
        if saved:
            flash(f"Company “{company.name}” created with logo.", "success")
        else:
            flash(f"Company “{company.name}” created.", "success")
        return redirect(url_for("admin.company_detail", company_id=company.id))
    if request.method == "POST":
        _flash_form_errors(form)
    return render_template("admin/company_form.html", form=form, company=None)


@admin_bp.route("/companies/<int:company_id>", methods=["GET", "POST"])
@super_admin_required
def company_detail(company_id: int):
    company = db.get_or_404(Company, company_id)
    form = CompanyForm(obj=company)
    user_form = CompanyUserForm()
    if request.method == "POST" and request.form.get("form_name") == "company":
        form = CompanyForm()
        if form.validate_on_submit():
            company.name = form.name.data.strip()
            company.notes = (form.notes.data or "").strip() or None
            company.is_active = bool(form.is_active.data)
            logo_file = request.files.get("logo") or form.logo.data
            saved = _save_logo(logo_file, company)
            try:
                db.session.commit()
            except Exception as exc:
                db.session.rollback()
                flash(f"Could not save company (logo column may be too small). Run migrate_schema.py. ({exc})", "danger")
                return redirect(url_for("admin.company_detail", company_id=company.id))
            for u in company.users:
                mirror_user_to_registry(u)
            if saved:
                flash("Company updated — logo saved for online portal branding.", "success")
            else:
                flash("Company updated.", "success")
            return redirect(url_for("admin.company_detail", company_id=company.id))
        _flash_form_errors(form)
    users = company.users.order_by(PortalUser.full_name).all()
    return render_template(
        "admin/company_detail.html",
        company=company,
        form=form,
        user_form=user_form,
        users=users,
    )


@admin_bp.route("/companies/<int:company_id>/toggle", methods=["POST"])
@super_admin_required
def company_toggle(company_id: int):
    company = db.get_or_404(Company, company_id)
    company.is_active = not company.is_active
    db.session.commit()
    for u in company.users:
        mirror_user_to_registry(u)
    state = "active" if company.is_active else "inactive"
    flash(f"Company marked {state}. Offline sync will enforce this when they connect.", "info")
    return redirect(request.referrer or url_for("admin.companies"))


@admin_bp.route("/companies/<int:company_id>/users", methods=["POST"])
@super_admin_required
def company_add_user(company_id: int):
    company = db.get_or_404(Company, company_id)
    form = CompanyUserForm()
    if not form.validate_on_submit():
        for field, errs in form.errors.items():
            for e in errs:
                flash(f"{field}: {e}", "danger")
        return redirect(url_for("admin.company_detail", company_id=company.id))

    username = form.username.data.strip()
    if PortalUser.query.filter(db.func.lower(PortalUser.username) == username.lower()).first():
        flash("Username already exists.", "danger")
        return redirect(url_for("admin.company_detail", company_id=company.id))

    user = PortalUser(
        company_id=company.id,
        username=username,
        email=(form.email.data or "").strip() or None,
        full_name=form.full_name.data.strip(),
        role=form.role.data,
        is_active_user=bool(form.is_active.data),
        license_type="trial",
        subscription_plan=form.subscription_plan.data or "lifetime",
    )
    user.set_password(form.password.data)
    user.ensure_registration_key()
    db.session.add(user)
    db.session.commit()
    mirror_user_to_registry(user)
    flash(
        f"User created. Registration key: {user.registration_key}",
        "success",
    )
    return redirect(url_for("admin.company_detail", company_id=company.id))


@admin_bp.route("/users")
@super_admin_required
def users():
    rows = (
        PortalUser.query.filter(PortalUser.role != PortalRole.SUPER_ADMIN.value)
        .order_by(PortalUser.full_name)
        .all()
    )
    return render_template("admin/users.html", users=rows)


@admin_bp.route("/users/<int:user_id>/toggle", methods=["POST"])
@super_admin_required
def user_toggle(user_id: int):
    user = db.get_or_404(PortalUser, user_id)
    if user.is_super_admin():
        flash("Cannot deactivate Super Admin here.", "danger")
        return redirect(url_for("admin.users"))
    user.is_active_user = not user.is_active_user
    db.session.commit()
    mirror_user_to_registry(user)
    state = "active" if user.is_active_user else "inactive"
    flash(f"{user.username} marked {state}.", "info")
    return redirect(request.referrer or url_for("admin.users"))


@admin_bp.route("/users/<int:user_id>/regen-key", methods=["POST"])
@super_admin_required
def user_regen_key(user_id: int):
    user = db.get_or_404(PortalUser, user_id)
    if user.is_super_admin():
        flash("Super Admin has no PC key.", "warning")
        return redirect(url_for("admin.users"))
    key = user.regenerate_key()
    db.session.commit()
    mirror_user_to_registry(user)
    flash(f"New key for {user.username}: {key}", "success")
    return redirect(request.referrer or url_for("admin.users"))

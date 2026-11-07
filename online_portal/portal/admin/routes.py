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

from portal.admin.forms import CompanyForm, CompanyUserForm
from portal.extensions import db
from portal.models import Company, PortalRole, PortalUser
from portal.services.registry_mirror import mirror_user_to_registry

admin_bp = Blueprint("admin", __name__)


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


def _save_logo(file_storage, company: Company) -> None:
    if not file_storage or not file_storage.filename:
        return
    upload_root = Path(current_app.config["UPLOAD_FOLDER"]) / "logos"
    upload_root.mkdir(parents=True, exist_ok=True)
    ext = Path(file_storage.filename).suffix.lower() or ".png"
    name = f"company_{company.id or 'new'}{ext}"
    path = upload_root / name
    data = file_storage.read()
    path.write_bytes(data)
    company.logo_path = f"logos/{name}"
    company.logo_data = data


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
        _save_logo(form.logo.data, company)
        db.session.commit()
        flash(f"Company “{company.name}” created.", "success")
        return redirect(url_for("admin.company_detail", company_id=company.id))
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
            _save_logo(form.logo.data, company)
            db.session.commit()
            # Mirror inactive flag to all company users in registry
            for u in company.users:
                mirror_user_to_registry(u)
            flash("Company updated.", "success")
            return redirect(url_for("admin.company_detail", company_id=company.id))
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
        flash("Cannot deactivate Super Admin.", "danger")
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

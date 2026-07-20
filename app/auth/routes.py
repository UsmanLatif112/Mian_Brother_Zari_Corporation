from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.extensions import db
from app.forms import LoginForm, UserForm
from app.models import User
from app.models.user import UserRole
from app.services.audit_service import log_audit
from app.utils.decorators import admin_required

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    form = LoginForm()
    auth_error = None
    if form.validate_on_submit():
        username = (form.username.data or "").strip()
        user = User.query.filter_by(username=username, is_deleted=False).first()
        if user and user.check_password(form.password.data) and user.is_active:
            login_user(user, remember=form.remember.data)
            from app.models.mixins import utcnow

            user.last_login_at = utcnow()
            log_audit("login", "user", user.id)
            db.session.commit()
            next_url = request.args.get("next") or url_for("dashboard.index")
            return redirect(next_url)
        auth_error = "Incorrect username or password. Please try again."
    return render_template(
        "auth/login.html",
        form=form,
        auth_error=auth_error,
        logged_out=request.args.get("logged_out") == "1",
    )


@auth_bp.route("/logout")
@login_required
def logout():
    log_audit("logout", "user", current_user.id)
    db.session.commit()
    logout_user()
    return redirect(url_for("auth.login", logged_out=1))


def _users_page(form=None, open_modal=False):
    users_list = User.query.filter_by(is_deleted=False).order_by(User.username).all()
    return render_template(
        "auth/users.html",
        users=users_list,
        form=form or UserForm(),
        open_modal=open_modal or request.args.get("open_modal") == "1",
    )


@auth_bp.route("/users")
@login_required
@admin_required
def users():
    return _users_page()


@auth_bp.route("/users/create", methods=["GET", "POST"])
@login_required
@admin_required
def create_user():
    if request.method == "GET":
        return redirect(url_for("auth.users", open_modal=1))
    form = UserForm()
    if form.validate_on_submit():
        user = User(
            username=form.username.data,
            email=form.email.data,
            full_name=form.full_name.data,
            role=UserRole(form.role.data),
            is_active_user=form.is_active_user.data,
        )
        user.set_password(form.password.data or "changeme")
        db.session.add(user)
        log_audit("create", "user", None, form.username.data)
        db.session.commit()
        flash("User created.", "success")
        return redirect(url_for("auth.users"))
    return _users_page(form=form, open_modal=True)

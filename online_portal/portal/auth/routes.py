from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from portal.auth.forms import LoginForm
from portal.extensions import db
from portal.models import PortalUser

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        if current_user.is_super_admin():
            return redirect(url_for("admin.dashboard"))
        return redirect(url_for("company.dashboard"))

    form = LoginForm()
    auth_error = None
    if form.validate_on_submit():
        username = (form.username.data or "").strip()
        user = PortalUser.query.filter(
            db.func.lower(PortalUser.username) == username.lower()
        ).first()
        if not user or not user.check_password(form.password.data or ""):
            auth_error = "Invalid username or password."
        elif not user.can_login_portal():
            auth_error = "This account is for the offline shop app only."
        elif not user.is_active:
            auth_error = "Account is inactive. Contact Super Admin."
        else:
            login_user(user, remember=bool(form.remember.data))
            user.last_login_at = datetime.utcnow()
            db.session.commit()
            nxt = request.args.get("next")
            if nxt:
                return redirect(nxt)
            if user.is_super_admin():
                return redirect(url_for("admin.dashboard"))
            return redirect(url_for("company.dashboard"))

    return render_template(
        "auth/login.html",
        form=form,
        auth_error=auth_error,
        logged_out=request.args.get("logout") == "1",
    )


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login", logout=1))

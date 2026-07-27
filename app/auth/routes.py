from flask import Blueprint, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.forms import ChangePasswordForm, LoginForm, USER_ROLE_CHOICES, UserEditForm, UserForm
from app.models import User
from app.models.mixins import utcnow
from app.models.user import PRIVILEGED_ROLES, UserRole
from app.services.audit_service import log_audit
from app.services.network_service import is_cloud_registry_reachable
from app.services.user_package_service import (
    build_user_data_package,
    ensure_user_data_package,
    package_exists,
)
from app.services.user_registry_service import (
    activate_user_on_mysql,
    delete_user_from_mysql,
    device_id,
    generate_registration_key,
    import_user_from_mysql_registry,
    mysql_configured,
    save_user_to_mysql,
)
from app.utils.decorators import super_admin_required

auth_bp = Blueprint("auth", __name__)


def _flash_first_form_error(form) -> None:
    for field, field_errors in form.errors.items():
        if field_errors:
            label = field.replace("_", " ").title()
            flash(f"{label}: {field_errors[0]}", "danger")
            return


def _wants_json() -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _availability_payload(username: str = "", email: str = "") -> dict:
    username = (username or "").strip()
    email = (email or "").strip()
    payload = {"ok": True, "username": None, "email": None}

    if username:
        active = User.query.filter(
            func.lower(User.username) == username.lower(),
            User.is_deleted.is_(False),
        ).first()
        deleted = User.query.filter(
            func.lower(User.username) == username.lower(),
            User.is_deleted.is_(True),
        ).first()
        if active:
            payload["username"] = {
                "available": False,
                "message": f"Username '{username}' already exists.",
            }
        elif deleted:
            payload["username"] = {
                "available": True,
                "restore": True,
                "message": "Deleted account found — saving will restore this user.",
            }
        else:
            payload["username"] = {"available": True, "message": None}

    if email:
        active = User.query.filter(
            func.lower(User.email) == email.lower(),
            User.is_deleted.is_(False),
        ).first()
        deleted = User.query.filter(
            func.lower(User.email) == email.lower(),
            User.is_deleted.is_(True),
        ).first()
        if active:
            payload["email"] = {
                "available": False,
                "message": f"Email '{email}' is already in use.",
            }
        elif deleted:
            payload["email"] = {
                "available": True,
                "restore": True,
                "message": "Deleted account found for this email — saving will restore it.",
            }
        else:
            payload["email"] = {"available": True, "message": None}

    return payload


def _json_or_flash(*, ok: bool, message: str, category: str = "success", field: str | None = None, errors: dict | None = None, status: int = 200, **extra):
    if _wants_json():
        body = {"ok": ok, "message": message, **extra}
        if field:
            body["field"] = field
        if errors:
            body["errors"] = errors
        return jsonify(body), status
    flash(message, category)
    return None


def _user_identity_conflict(
    username: str,
    email: str,
    *,
    exclude_user_id: int | None = None,
) -> str | None:
    """Return a friendly error if username/email is already used."""
    username = (username or "").strip()
    email = (email or "").strip()
    if not username:
        return "Username is required."

    user_q = User.query.filter(
        func.lower(User.username) == username.lower(),
        User.is_deleted.is_(False),
    )
    email_q = User.query.filter(
        func.lower(User.email) == email.lower(),
        User.is_deleted.is_(False),
    )
    if exclude_user_id:
        user_q = user_q.filter(User.id != exclude_user_id)
        email_q = email_q.filter(User.id != exclude_user_id)

    existing_user = user_q.first()
    if existing_user:
        return f"Username '{username}' already exists. Edit that user or pick another username."

    if email:
        existing_email = email_q.first()
        if existing_email:
            return f"Email '{email}' is already in use."

    return None


def _find_deleted_user_to_restore(username: str, email: str) -> User | None:
    username = (username or "").strip()
    email = (email or "").strip()
    by_username = User.query.filter(
        func.lower(User.username) == username.lower(),
        User.is_deleted.is_(True),
    ).first()
    if by_username:
        return by_username
    if email:
        return User.query.filter(
            func.lower(User.email) == email.lower(),
            User.is_deleted.is_(True),
        ).first()
    return None


def _resolve_edit_role(user: User, role_value: str | None) -> UserRole | None:
    if user.is_super_admin():
        return UserRole.SUPER_ADMIN
    value = (role_value or "").strip()
    allowed = {choice[0] for choice in USER_ROLE_CHOICES}
    if value not in allowed:
        return None
    return UserRole(value)


def _role_needs_registration_key(role: UserRole) -> bool:
    return role not in PRIVILEGED_ROLES


def _apply_user_registration(user: User, role: UserRole, password: str) -> tuple[bool, str | None]:
    """Set password and registration fields. Returns (needs_key, registration_key)."""
    user.role = role
    user.set_password(password)
    if _role_needs_registration_key(role):
        key = user.registration_key or generate_registration_key()
        user.registration_key = key
        user.is_registered = False
        user.registered_at = None
        user.device_id = None
        return True, key
    user.registration_key = None
    user.is_registered = True
    if not user.registered_at:
        user.registered_at = utcnow()
    user.device_id = None
    return False, None


def _apply_registration_for_role(user: User, role: UserRole) -> None:
    if role in PRIVILEGED_ROLES:
        user.is_registered = True
        user.registration_key = None
        if not user.registered_at:
            user.registered_at = utcnow()
    elif not user.registration_key:
        user.registration_key = generate_registration_key()
        user.is_registered = False
        user.registered_at = None


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    form = LoginForm()
    auth_error = None
    if form.validate_on_submit():
        username = (form.username.data or "").strip()
        user = User.query.filter(
            func.lower(User.username) == username.lower(),
            User.is_deleted.is_(False),
        ).first()
        if user and user.check_password(form.password.data) and user.is_active:
            login_user(user, remember=form.remember.data)
            user.last_login_at = utcnow()
            log_audit("login", "user", user.id)
            db.session.commit()
            next_url = request.args.get("next") or url_for("dashboard.index")
            return redirect(next_url)

        # New PC: pull account from MySQL cloud registry into local SQLite (one-time, needs internet).
        if not user and mysql_configured() and is_cloud_registry_reachable():
            imported = import_user_from_mysql_registry(username, form.password.data or "")
            if imported:
                db.session.add(imported)
                db.session.flush()
                login_user(imported, remember=form.remember.data)
                imported.last_login_at = utcnow()
                log_audit("login", "user", imported.id, "imported_from_mysql")
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


@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
@super_admin_required
def change_password():
    form = ChangePasswordForm()
    wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            msg = "Current password is incorrect."
            if wants_json:
                return jsonify({"ok": False, "error": msg, "field": "current_password"}), 400
            flash(msg, "danger")
        else:
            current_user.set_password(form.new_password.data)
            log_audit("change_password", "user", current_user.id)
            db.session.commit()
            msg = "Password updated successfully."
            if wants_json:
                return jsonify(
                    {
                        "ok": True,
                        "message": msg,
                        "redirect": url_for("dashboard.index"),
                    }
                )
            flash(msg, "success")
            return redirect(url_for("dashboard.index"))

    if wants_json and request.method == "POST":
        errors = {field: msgs[0] for field, msgs in form.errors.items() if msgs}
        first_error = next(iter(errors.values()), "Please fix the form errors.")
        field = next(iter(errors.keys()), None)
        return jsonify({"ok": False, "error": first_error, "errors": form.errors, "field": field}), 400

    return render_template("auth/change_password.html", form=form)


@auth_bp.route("/validate-password", methods=["POST"])
@login_required
def validate_password():
    from app.utils.password_policy import password_policy_errors, passwords_match

    data = request.get_json(silent=True) or {}
    password = (data.get("password") or "").strip()
    confirm = data.get("confirm")
    required = bool(data.get("required", True))

    if not required and not password:
        return jsonify({"ok": True, "errors": []})

    errors = password_policy_errors(password if required or password else "")
    if not errors and confirm is not None and not passwords_match(password, confirm):
        errors.append("Passwords do not match.")

    if errors:
        return jsonify({"ok": False, "errors": errors}), 400
    return jsonify({"ok": True, "errors": []})


@auth_bp.route("/register", methods=["POST"])
@login_required
def register():
    """Verify key against MySQL, then mark registered in MySQL + local SQLite."""
    if current_user.is_registration_complete():
        return jsonify({"ok": True, "message": "Already registered."})

    if not mysql_configured():
        return jsonify(
            {
                "ok": False,
                "error": "Cloud registry (MySQL) is not configured on this system.",
            }
        ), 503

    if not is_cloud_registry_reachable():
        return jsonify(
            {
                "ok": False,
                "error": "Cannot reach cloud registry. Check your internet connection and try again.",
                "offline": True,
            }
        ), 400

    data = request.get_json(silent=True) or {}
    key = (data.get("key") or request.form.get("key") or "").strip()
    if not key:
        return jsonify({"ok": False, "error": "Registration key is required."}), 400

    machine_id = device_id()
    try:
        result = activate_user_on_mysql(current_user.username, key, machine_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception:
        return jsonify(
            {"ok": False, "error": "Could not reach cloud registry. Try again later."}
        ), 503

    if not result.get("ok"):
        return jsonify({"ok": False, "error": result.get("error", "Registration failed.")}), 400

    now = utcnow()
    current_user.is_registered = True
    current_user.registered_at = now
    current_user.device_id = machine_id
    current_user.registration_key = result.get("registration_key") or key
    if result.get("cloud_id"):
        current_user.remote_id = result["cloud_id"]
    log_audit("register", "user", current_user.id, f"device={machine_id}")
    db.session.commit()
    return jsonify(
        {
            "ok": True,
            "message": "This computer is registered. You can use the app offline from now on.",
        }
    )


def _users_page(form=None, open_modal=False):
    users_list = User.query.filter_by(is_deleted=False).order_by(User.username).all()
    package_flags = {
        u.id: package_exists(u.username)
        for u in users_list
        if not u.is_super_admin()
    }
    return render_template(
        "auth/users.html",
        users=users_list,
        form=form or UserForm(),
        open_modal=open_modal or request.args.get("open_modal") == "1",
        mysql_configured=mysql_configured(),
        package_flags=package_flags,
    )


@auth_bp.route("/users")
@login_required
@super_admin_required
def users():
    return _users_page()


@auth_bp.route("/users/check")
@login_required
@super_admin_required
def check_user_availability():
    username = (request.args.get("username") or "").strip()
    email = (request.args.get("email") or "").strip()
    if not username and not email:
        return jsonify({"ok": False, "error": "Username or email is required."}), 400
    return jsonify(_availability_payload(username, email))


@auth_bp.route("/users/create", methods=["GET", "POST"])
@login_required
@super_admin_required
def create_user():
    if request.method == "GET":
        return redirect(url_for("auth.users", open_modal=1))
    form = UserForm()
    if form.validate_on_submit():
        username = (form.username.data or "").strip()
        email = (form.email.data or "").strip()
        conflict = _user_identity_conflict(username, email)
        if conflict:
            field = "username" if "username" in conflict.lower() else "email"
            resp = _json_or_flash(ok=False, message=conflict, category="danger", field=field, status=400)
            if resp:
                return resp
            return _users_page(form=form, open_modal=True)

        role = UserRole(form.role.data)
        needs_key = _role_needs_registration_key(role)

        restored = _find_deleted_user_to_restore(username, email)
        if restored:
            user = restored
            user.is_deleted = False
            user.deleted_at = None
            user.username = username
            user.email = email
            user.full_name = form.full_name.data
            user.is_active_user = form.is_active_user.data
            restored_msg = True
        else:
            user = User(
                username=username,
                email=email,
                full_name=form.full_name.data,
                is_active_user=form.is_active_user.data,
            )
            restored_msg = False
            db.session.add(user)

        needs_key, registration_key = _apply_user_registration(
            user, role, form.password.data or ""
        )

        db.session.flush()
        log_audit("create", "user", user.id, username)

        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            msg = f"Username '{username}' or email '{email}' is already in use."
            resp = _json_or_flash(ok=False, message=msg, category="danger", field="username", status=400)
            if resp:
                return resp
            return _users_page(form=form, open_modal=True)

        mysql_warning = None
        if needs_key:
            if not mysql_configured():
                mysql_warning = (
                    "User saved in SQLite only. Set MYSQL_DATABASE_URI so the registration key "
                    "is stored in cloud for PC activation."
                )
            elif not is_cloud_registry_reachable():
                mysql_warning = (
                    "User saved in SQLite. Connect to the internet and create again or edit "
                    "to push the key to MySQL."
                )
            else:
                try:
                    cloud_id = save_user_to_mysql(user, registration_key=registration_key or "")
                    user.remote_id = cloud_id
                    db.session.commit()
                except Exception as exc:
                    mysql_warning = f"User saved in SQLite but MySQL sync failed: {exc}"

        if needs_key:
            msg = f"User created. Registration key: {registration_key}"
            if restored_msg:
                msg = f"User restored. Registration key: {registration_key}"
        else:
            msg = "User restored." if restored_msg else "User created."

        if mysql_warning:
            msg = f"{msg} {mysql_warning}"

        package_url = None
        package_filename = None
        package_warning = None
        if not user.is_super_admin():
            try:
                pkg = build_user_data_package(user)
                package_filename = pkg.get("filename")
                package_url = url_for("auth.download_user_package", user_id=user.id)
                msg = f"{msg} Data package ready: {package_filename}"
            except Exception as exc:
                package_warning = f"Could not build data package: {exc}"
                msg = f"{msg} {package_warning}"

        resp = _json_or_flash(
            ok=True,
            message=msg,
            category="success" if not (mysql_warning or package_warning) else "warning",
            restored=restored_msg,
            registration_key=registration_key,
            redirect=url_for("auth.users"),
            warning=bool(mysql_warning or package_warning),
            package_url=package_url,
            package_filename=package_filename,
            user_id=user.id,
        )
        if resp:
            return resp
        flash(msg, "success" if not (mysql_warning or package_warning) else "warning")
        return redirect(url_for("auth.users"))

    if _wants_json():
        errors = {field: msgs for field, msgs in form.errors.items() if msgs}
        first_field = next(iter(errors.keys()), None)
        first_error = next((msgs[0] for msgs in errors.values() if msgs), "Please fix the form errors.")
        return jsonify({"ok": False, "error": first_error, "errors": errors, "field": first_field}), 400

    _flash_first_form_error(form)
    return _users_page(form=form, open_modal=True)


@auth_bp.route("/users/<int:user_id>/package")
@login_required
@super_admin_required
def download_user_package(user_id):
    user = User.query.filter_by(id=user_id, is_deleted=False).first_or_404()
    if user.is_super_admin():
        flash("Super Admin does not need a staff data package.", "warning")
        return redirect(url_for("auth.users"))
    force = request.args.get("rebuild") == "1"
    try:
        pkg = ensure_user_data_package(user, force=force)
    except Exception as exc:
        flash(f"Could not build data package: {exc}", "danger")
        return redirect(url_for("auth.users"))
    return send_file(
        pkg["zip_path"],
        as_attachment=True,
        download_name=pkg["filename"],
        mimetype="application/zip",
    )


@auth_bp.route("/users/<int:user_id>/package/rebuild", methods=["POST"])
@login_required
@super_admin_required
def rebuild_user_package(user_id):
    user = User.query.filter_by(id=user_id, is_deleted=False).first_or_404()
    if user.is_super_admin():
        return jsonify({"ok": False, "error": "Super Admin does not need a staff data package."}), 400
    try:
        pkg = build_user_data_package(user)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify(
        {
            "ok": True,
            "message": f"Package rebuilt: {pkg['filename']}",
            "package_url": url_for("auth.download_user_package", user_id=user.id),
            "package_filename": pkg["filename"],
        }
    )


@auth_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@super_admin_required
def edit_user(user_id):
    user = User.query.filter_by(id=user_id, is_deleted=False).first_or_404()
    if user.is_super_admin() and user.id != current_user.id:
        flash("Super Admin account cannot be edited.", "danger")
        return redirect(url_for("auth.users"))

    form = UserEditForm(obj=user)
    if not user.is_super_admin():
        form.role.data = user.role.value
    if form.validate_on_submit():
        role = _resolve_edit_role(user, form.role.data)
        if role is None:
            flash("Please select a valid role.", "danger")
            return render_template("auth/edit_user.html", edit_user=user, edit_form=form)

        user.email = form.email.data
        user.full_name = form.full_name.data
        user.role = role
        user.is_active_user = form.is_active_user.data

        conflict = _user_identity_conflict(
            user.username,
            form.email.data or "",
            exclude_user_id=user.id,
        )
        if conflict:
            flash(conflict, "danger")
            return render_template("auth/edit_user.html", edit_user=user, edit_form=form)

        if form.password.data:
            user.set_password(form.password.data)

        if not user.is_super_admin():
            _apply_registration_for_role(user, role)

        log_audit("update", "user", user.id, user.username)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("Email is already in use by another account.", "danger")
            return render_template("auth/edit_user.html", edit_user=user, edit_form=form)
        flash("User updated.", "success")
        return redirect(url_for("auth.users"))

    _flash_first_form_error(form)
    return render_template("auth/edit_user.html", edit_user=user, edit_form=form)


@auth_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@login_required
@super_admin_required
def delete_user(user_id):
    user = User.query.filter_by(id=user_id, is_deleted=False).first_or_404()
    if user.id == current_user.id:
        flash("You cannot delete your own account.", "danger")
        return redirect(url_for("auth.users"))
    if user.is_super_admin():
        flash("Super Admin account cannot be deleted.", "danger")
        return redirect(url_for("auth.users"))

    user.soft_delete()
    user.is_active_user = False
    user.remote_id = None

    cloud_deleted = True
    if mysql_configured():
        try:
            cloud_deleted = delete_user_from_mysql(user.username)
        except Exception:
            cloud_deleted = False

    log_audit("delete", "user", user.id, user.username)
    db.session.commit()
    if cloud_deleted:
        flash("User deleted from app and cloud registry.", "success")
    elif mysql_configured():
        flash(
            "User deleted locally, but could not remove from MySQL. Check internet and try again.",
            "warning",
        )
    else:
        flash("User deleted.", "success")
    return redirect(url_for("auth.users"))

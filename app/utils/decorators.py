import logging
from functools import wraps

from flask import abort, flash, redirect, request, url_for
from flask_login import current_user

logger = logging.getLogger(__name__)


def permission_required(permission: str):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login", next=request.url))
            if not current_user.has_permission(permission):
                flash("You do not have permission to access this resource.", "danger")
                abort(403)
            return f(*args, **kwargs)

        return wrapped

    return decorator


def admin_required(f):
    return permission_required("*")(f)

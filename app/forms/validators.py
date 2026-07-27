from wtforms.validators import ValidationError

from app.utils.password_policy import PASSWORD_POLICY_MESSAGE, password_policy_errors


class StrongPassword:
    """Require strong password when field has a value."""

    def __init__(self, message: str | None = None, required: bool = True):
        self.message = message or PASSWORD_POLICY_MESSAGE
        self.required = required

    def __call__(self, form, field):
        value = (field.data or "").strip()
        if not value:
            if self.required:
                raise ValidationError("Password is required.")
            return
        errors = password_policy_errors(value)
        if errors:
            raise ValidationError(errors[0])

"""Shared password strength rules for server and client validation."""

from __future__ import annotations

import re

PASSWORD_MIN_LENGTH = 8
PASSWORD_POLICY_SHORT = (
    "Min 8 characters, one uppercase, one number, and one special character."
)
PASSWORD_POLICY_MESSAGE = (
    "Password must be at least 8 characters and include one uppercase letter, "
    "one number, and one special character."
)


def password_policy_errors(password: str | None) -> list[str]:
    if password is None:
        password = ""
    pwd = password.strip()
    if not pwd:
        return ["Password is required."]
    errors: list[str] = []
    if len(pwd) < PASSWORD_MIN_LENGTH:
        errors.append("Password must be at least 8 characters.")
    if not re.search(r"[A-Z]", pwd):
        errors.append("Password must include at least one uppercase letter.")
    if not re.search(r"\d", pwd):
        errors.append("Password must include at least one number.")
    if not re.search(r"[^\w\s]", pwd):
        errors.append("Password must include at least one special character.")
    return errors


def is_strong_password(password: str | None) -> bool:
    return not password_policy_errors(password)


def passwords_match(password: str | None, confirm: str | None) -> bool:
    return (password or "") == (confirm or "")

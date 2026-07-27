"""Quick internet reachability checks (non-blocking, short timeout)."""

from __future__ import annotations

import socket


def is_internet_available(timeout: float = 2.0) -> bool:
    """
    Best-effort check that the PC can reach the public internet / Google OAuth.

    Used before opening Google sign-in so the desktop app stays on the ERP page when offline.
    """
    targets = (
        ("accounts.google.com", 443),
        ("www.google.com", 443),
        ("8.8.8.8", 53),
    )
    for host, port in targets:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False

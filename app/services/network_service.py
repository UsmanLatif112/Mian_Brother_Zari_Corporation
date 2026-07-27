"""Quick internet reachability checks (non-blocking, short timeout)."""

from __future__ import annotations

import ipaddress
import os
import socket
from typing import Iterable


def _mysql_host_port() -> tuple[str, int] | None:
    """Resolve MySQL host/port from env (used for registration + sync probes)."""
    host = (os.environ.get("MYSQL_HOST") or "").strip()
    if host:
        try:
            port = int(os.environ.get("MYSQL_PORT") or 3306)
        except (TypeError, ValueError):
            port = 3306
        return host, port

    uri = (os.environ.get("MYSQL_DATABASE_URI") or "").strip()
    if not uri:
        return None
    try:
        from sqlalchemy.engine import make_url

        url = make_url(uri)
        if url.host:
            return url.host, int(url.port or 3306)
    except Exception:
        pass
    return None


def _tcp_reachable(host: str, port: int, timeout: float) -> bool:
    """Try TCP connect, preferring IPv4 when DNS returns both families."""
    try:
        port = int(port)
    except (TypeError, ValueError):
        return False

    # Literal IPs skip DNS.
    try:
        ip = ipaddress.ip_address(host)
        family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            sock.settimeout(timeout)
            sock.connect((str(ip), port))
            return True
        except OSError:
            return False
        finally:
            sock.close()
    except ValueError:
        pass

    families = (socket.AF_INET, socket.AF_INET6)
    for family in families:
        try:
            addrs = socket.getaddrinfo(host, port, family, socket.SOCK_STREAM)
        except OSError:
            continue
        for _af, _type, _proto, _canon, sockaddr in addrs:
            sock = socket.socket(_af, _type, _proto)
            try:
                sock.settimeout(timeout)
                sock.connect(sockaddr)
                return True
            except OSError:
                continue
            finally:
                sock.close()
    return False


def _any_tcp_reachable(targets: Iterable[tuple[str, int]], timeout: float) -> bool:
    for host, port in targets:
        if _tcp_reachable(host, port, timeout):
            return True
    return False


def is_cloud_registry_reachable(timeout: float = 3.0) -> bool:
    """
    True when the cloud user registry (MySQL) can be reached.

    Registration and first-time login on a new PC depend on this — not on Google.
    """
    endpoint = _mysql_host_port()
    if endpoint and _tcp_reachable(endpoint[0], endpoint[1], timeout):
        return True

    try:
        from flask import has_app_context

        if has_app_context():
            from app.services.sync_service import is_mysql_available

            if is_mysql_available(force=True, timeout=timeout):
                return True
    except Exception:
        pass

    return False


def is_internet_available(timeout: float = 3.0) -> bool:
    """
    Best-effort check that the PC can reach the public internet / Google OAuth.

    Used before opening Google sign-in so the desktop app stays on the ERP page when offline.
    """
    targets: list[tuple[str, int]] = []

    mysql = _mysql_host_port()
    if mysql:
        targets.append(mysql)

    targets.extend(
        (
            ("1.1.1.1", 443),
            ("8.8.8.8", 53),
            ("www.google.com", 443),
            ("accounts.google.com", 443),
        )
    )
    return _any_tcp_reachable(targets, timeout)

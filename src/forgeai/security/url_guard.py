"""SSRF protection and host allowlist enforcement for outbound HTTP tools and URL ingestion."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable
from urllib.parse import urlparse

ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})

# Cloud metadata hostnames & internal names always blocked unless allow_private=True
_BLOCKED_HOSTNAMES: frozenset[str] = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata.google.internal",
        "metadata",
        "instance-data",
    }
)


class URLValidationError(ValueError):
    """Raised when a URL fails scheme, host allowlist, or SSRF IP validation."""


def _is_disallowed_ip(ip_str: str) -> bool:
    addr = ipaddress.ip_address(ip_str)
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def _matches_allowlist(host: str, allowed_hosts: Iterable[str]) -> bool:
    normalized = [h.strip().lower().rstrip(".") for h in allowed_hosts if h.strip()]
    if not normalized:
        return False
    for pattern in normalized:
        if pattern.startswith("*."):
            suffix = pattern[1:]  # ".example.com"
            if host.endswith(suffix) and len(host) > len(suffix):
                return True
        elif host == pattern:
            return True
    return False


def validate_url(
    url: str,
    *,
    allow_private: bool = False,
    resolve_dns: bool = True,
    allowed_hosts: Iterable[str] | None = None,
) -> str:
    """Validate URL scheme, optional host allowlist, and block private/internal SSRF targets."""
    candidate = url.strip()
    if not candidate:
        raise URLValidationError("URL cannot be empty.")
    if len(candidate) > 2048:
        raise URLValidationError("URL exceeds 2048 characters.")

    parsed = urlparse(candidate)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise URLValidationError(
            f"Disallowed URL scheme {parsed.scheme!r}. Only http and https are permitted."
        )
    if not parsed.hostname:
        raise URLValidationError("URL must include a hostname.")
    if parsed.username or parsed.password:
        raise URLValidationError("Credentials embedded in URLs are not permitted.")

    host = parsed.hostname.lower().rstrip(".")

    if allowed_hosts is not None and not _matches_allowlist(host, allowed_hosts):
        raise URLValidationError(
            f"Host {host!r} is not in the configured HTTP host allowlist."
        )

    if not allow_private:
        if host in _BLOCKED_HOSTNAMES or host.endswith(".internal") or host.endswith(".local"):
            raise URLValidationError(f"Blocked internal hostname: {host!r}")

        # Direct IP literal check
        ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address | None = None
        try:
            ip_obj = ipaddress.ip_address(host)
        except ValueError:
            ip_obj = None

        if ip_obj is not None:
            if _is_disallowed_ip(str(ip_obj)):
                raise URLValidationError(f"Blocked private or reserved IP address: {host!r}")
            return candidate

        if resolve_dns:
            try:
                infos = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
            except socket.gaierror as exc:
                raise URLValidationError(f"Failed to resolve hostname {host!r}: {exc}") from exc

            for info in infos:
                resolved_ip = str(info[4][0])
                if _is_disallowed_ip(resolved_ip):
                    raise URLValidationError(
                        f"Hostname {host!r} resolved to disallowed IP address {resolved_ip!r}."
                    )

    return candidate

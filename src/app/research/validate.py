"""Input validation for URL and local-file ingestion.

Security boundary: SSRF protection uses pre-resolution of the hostname via
socket.getaddrinfo(). This closes opportunistic attacks and non-adversarial
misconfiguration but does not fully prevent DNS rebinding by an attacker who
controls DNS TTL. Custom connect-time IP pinning is deferred as hardening.
"""

from __future__ import annotations

import ipaddress
import socket
import stat
from pathlib import Path
from urllib.parse import urlparse

from app.research.constants import (
    ALLOWED_FILE_EXTENSIONS,
    ALLOWED_URL_SCHEMES,
    FILE_MAX_BYTES,
)
from app.research.errors import SecurityError

# ---------------------------------------------------------------------------
# Blocked IP networks — IPv4 and IPv6.
# ---------------------------------------------------------------------------

_BLOCKED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = tuple(
    ipaddress.ip_network(n)
    for n in [
        # IPv4
        "0.0.0.0/8",          # This network (RFC 1122)
        "10.0.0.0/8",         # RFC 1918
        "100.64.0.0/10",      # Shared address space (RFC 6598)
        "127.0.0.0/8",        # Loopback
        "169.254.0.0/16",     # Link-local (RFC 3927)
        "172.16.0.0/12",      # RFC 1918
        "192.0.0.0/24",       # IETF protocol assignments (RFC 5737)
        "192.0.2.0/24",       # Documentation TEST-NET-1 (RFC 5737)
        "192.168.0.0/16",     # RFC 1918
        "198.18.0.0/15",      # Benchmarking (RFC 2544)
        "198.51.100.0/24",    # Documentation TEST-NET-2 (RFC 5737)
        "203.0.113.0/24",     # Documentation TEST-NET-3 (RFC 5737)
        "224.0.0.0/4",        # Multicast (RFC 3171)
        "233.252.0.0/24",     # Documentation multicast (RFC 5771)
        "240.0.0.0/4",        # Reserved (RFC 1112)
        "255.255.255.255/32", # Broadcast
        # IPv6
        "::1/128",            # Loopback
        "::/128",             # Unspecified
        "fc00::/7",           # Unique local (RFC 4193)
        "fe80::/10",          # Link-local (RFC 4291)
        "ff00::/8",           # Multicast (RFC 4291)
        "2001:db8::/32",      # Documentation (RFC 3849)
        "100::/64",           # Discard prefix (RFC 6666)
    ]
)


def is_blocked_address(addr: str) -> bool:
    """Return True if *addr* falls within any blocked IP range."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return True  # unparseable address is treated as blocked
    return any(ip in net for net in _BLOCKED_NETWORKS)


def validate_url(url: str) -> None:
    """Validate *url* for scheme, structure, userinfo, and SSRF risk.

    Raises :exc:`~app.research.errors.SecurityError` on any violation.
    All checks run before any Source row is created.

    MVP SSRF boundary: hostname is resolved via socket.getaddrinfo() and
    every returned address is checked against blocked ranges. This does not
    prevent DNS rebinding by an adversary who controls TTL. Connect-time IP
    pinning is deferred.
    """
    try:
        parsed = urlparse(url)
    except Exception as exc:
        raise SecurityError(f"Malformed URL: {exc}") from exc

    scheme = parsed.scheme.lower()
    if scheme not in ALLOWED_URL_SCHEMES:
        raise SecurityError(
            f"URL scheme {scheme!r} is not allowed. Only 'http' and 'https' are permitted."
        )

    if not parsed.hostname:
        raise SecurityError("URL has no hostname.")

    if parsed.username is not None or parsed.password is not None:
        raise SecurityError(
            "URL contains userinfo (username or password), which is not allowed."
        )

    host = parsed.hostname
    port = parsed.port or (443 if scheme == "https" else 80)

    try:
        addr_infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise SecurityError(f"Could not resolve hostname {host!r}: {exc}") from exc

    if not addr_infos:
        raise SecurityError(f"Hostname {host!r} resolved to no addresses.")

    for _family, _type, _proto, _canonname, sockaddr in addr_infos:
        ip_str = sockaddr[0]
        if is_blocked_address(ip_str):
            raise SecurityError(
                f"URL resolves to a blocked address ({ip_str}). "
                "Private, loopback, link-local, and reserved ranges are not allowed."
            )


def validate_file_path(path_str: str) -> Path:
    """Validate *path_str* as an ingestible local file.

    Returns the resolved :class:`~pathlib.Path` on success.
    Raises :exc:`~app.research.errors.SecurityError` or :exc:`ValueError` on violation.
    All checks run before any Source row is created.
    """
    if "\x00" in path_str:
        raise SecurityError("File path contains a null byte.")

    resolved = Path(path_str).resolve()

    try:
        st = resolved.stat()
    except FileNotFoundError as err:
        raise ValueError(f"File not found: {resolved}") from err
    except PermissionError as err:
        raise ValueError(f"Permission denied reading: {resolved}") from err

    if not stat.S_ISREG(st.st_mode):
        raise ValueError(
            f"{resolved} is not a regular file "
            f"(mode={oct(st.st_mode)}). Devices, directories, FIFOs, and sockets are not allowed."
        )

    suffix = resolved.suffix.lower()
    if suffix not in ALLOWED_FILE_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_FILE_EXTENSIONS))
        raise ValueError(
            f"Unsupported file extension {suffix!r}. Allowed: {allowed}"
        )

    if st.st_size > FILE_MAX_BYTES:
        mb = st.st_size / (1024 * 1024)
        raise ValueError(
            f"File exceeds {FILE_MAX_BYTES // (1024 * 1024)} MB limit "
            f"(actual: {mb:.1f} MB): {resolved}"
        )

    return resolved

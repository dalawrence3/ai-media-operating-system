"""Tests for URL and file-path validation including SSRF protection."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.research.errors import SecurityError
from app.research.validate import is_blocked_address, validate_file_path, validate_url

# ---------------------------------------------------------------------------
# IP blocking — unit tests (no network)
# ---------------------------------------------------------------------------


class TestIsBlockedAddress:
    @pytest.mark.parametrize("addr", [
        "10.0.0.1",
        "10.255.255.255",
        "172.16.0.1",
        "172.31.255.255",
        "192.168.0.1",
        "192.168.255.255",
    ])
    def test_rfc1918_blocked(self, addr: str):
        assert is_blocked_address(addr) is True

    @pytest.mark.parametrize("addr", [
        "127.0.0.1",
        "127.0.0.2",
        "127.255.255.255",
    ])
    def test_loopback_blocked(self, addr: str):
        assert is_blocked_address(addr) is True

    def test_link_local_blocked(self):
        assert is_blocked_address("169.254.0.1") is True
        assert is_blocked_address("169.254.255.255") is True

    def test_multicast_blocked(self):
        assert is_blocked_address("224.0.0.1") is True
        assert is_blocked_address("239.255.255.255") is True

    def test_documentation_ranges_blocked(self):
        assert is_blocked_address("192.0.2.1") is True       # TEST-NET-1
        assert is_blocked_address("198.51.100.1") is True    # TEST-NET-2
        assert is_blocked_address("203.0.113.1") is True     # TEST-NET-3

    def test_shared_address_space_blocked(self):
        assert is_blocked_address("100.64.0.1") is True
        assert is_blocked_address("100.127.255.255") is True

    def test_broadcast_blocked(self):
        assert is_blocked_address("255.255.255.255") is True

    def test_this_network_blocked(self):
        assert is_blocked_address("0.0.0.1") is True

    # IPv6
    def test_ipv6_loopback_blocked(self):
        assert is_blocked_address("::1") is True

    def test_ipv6_unspecified_blocked(self):
        assert is_blocked_address("::") is True

    def test_ipv6_unique_local_blocked(self):
        assert is_blocked_address("fc00::1") is True
        assert is_blocked_address("fd00::1") is True

    def test_ipv6_link_local_blocked(self):
        assert is_blocked_address("fe80::1") is True

    def test_ipv6_multicast_blocked(self):
        assert is_blocked_address("ff02::1") is True

    def test_ipv6_documentation_blocked(self):
        assert is_blocked_address("2001:db8::1") is True

    @pytest.mark.parametrize("addr", [
        "1.1.1.1",
        "8.8.8.8",
        "93.184.216.34",
        "151.101.1.140",
    ])
    def test_public_addresses_allowed(self, addr: str):
        assert is_blocked_address(addr) is False


# ---------------------------------------------------------------------------
# URL validation — mocking getaddrinfo to avoid live DNS
# ---------------------------------------------------------------------------


def _mock_getaddrinfo_public(host, port, **kwargs):
    return [(None, None, None, None, ("1.1.1.1", port))]


def _mock_getaddrinfo_private(host, port, **kwargs):
    return [(None, None, None, None, ("192.168.1.1", port))]


class TestValidateUrl:
    def test_http_scheme_accepted(self):
        with patch("app.research.validate.socket.getaddrinfo", _mock_getaddrinfo_public):
            validate_url("http://example.com/page")  # must not raise

    def test_https_scheme_accepted(self):
        with patch("app.research.validate.socket.getaddrinfo", _mock_getaddrinfo_public):
            validate_url("https://example.com/page")  # must not raise

    def test_ftp_scheme_rejected(self):
        with pytest.raises(SecurityError, match="scheme"):
            validate_url("ftp://example.com/file")

    def test_file_scheme_rejected(self):
        with pytest.raises(SecurityError, match="scheme"):
            validate_url("file:///etc/passwd")

    def test_javascript_scheme_rejected(self):
        with pytest.raises(SecurityError, match="scheme"):
            validate_url("javascript:alert(1)")

    def test_no_scheme_rejected(self):
        with pytest.raises(SecurityError):
            validate_url("example.com/page")

    def test_userinfo_username_rejected(self):
        with pytest.raises(SecurityError, match="userinfo"):
            validate_url("http://user@example.com/")

    def test_userinfo_password_rejected(self):
        with pytest.raises(SecurityError, match="userinfo"):
            validate_url("http://user:pass@example.com/")

    def test_ssrf_private_ip_rejected(self):
        with patch("app.research.validate.socket.getaddrinfo", _mock_getaddrinfo_private):
            with pytest.raises(SecurityError, match="blocked"):
                validate_url("http://internal.example.com/")

    def test_dns_resolution_failure_rejected(self):
        import socket
        with patch(
            "app.research.validate.socket.getaddrinfo",
            side_effect=socket.gaierror("NXDOMAIN"),
        ):
            with pytest.raises(SecurityError, match="resolve"):
                validate_url("http://nonexistent.invalid/")

    def test_public_url_accepted(self):
        with patch("app.research.validate.socket.getaddrinfo", _mock_getaddrinfo_public):
            validate_url("https://example.com/article?id=1")  # must not raise

    def test_empty_string_rejected(self):
        with pytest.raises(SecurityError):
            validate_url("")


# ---------------------------------------------------------------------------
# File-path validation
# ---------------------------------------------------------------------------


class TestValidateFilePath:
    def test_valid_txt_file(self, tmp_path: Path):
        f = tmp_path / "doc.txt"
        f.write_text("hello world")
        result = validate_file_path(str(f))
        assert result == f.resolve()

    def test_valid_md_file(self, tmp_path: Path):
        f = tmp_path / "doc.md"
        f.write_text("# Title\n\nBody.")
        result = validate_file_path(str(f))
        assert result.suffix == ".md"

    def test_valid_pdf_file(self, tmp_path: Path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4 fake")
        result = validate_file_path(str(f))
        assert result.suffix == ".pdf"

    def test_null_byte_rejected(self):
        with pytest.raises(SecurityError, match="null byte"):
            validate_file_path("/tmp/file\x00.txt")

    def test_missing_file_rejected(self):
        with pytest.raises(ValueError, match="not found"):
            validate_file_path("/nonexistent/path/file.txt")

    def test_directory_rejected(self, tmp_path: Path):
        with pytest.raises(ValueError, match="not a regular file"):
            validate_file_path(str(tmp_path))

    def test_unsupported_extension_rejected(self, tmp_path: Path):
        f = tmp_path / "doc.docx"
        f.write_bytes(b"fake docx")
        with pytest.raises(ValueError, match="extension"):
            validate_file_path(str(f))

    def test_oversized_file_rejected(self, tmp_path: Path):
        from app.research.constants import FILE_MAX_BYTES

        f = tmp_path / "big.txt"
        f.write_bytes(b"x" * (FILE_MAX_BYTES + 1))
        with pytest.raises(ValueError, match="MB"):
            validate_file_path(str(f))

    def test_path_traversal_resolved(self, tmp_path: Path):
        f = tmp_path / "doc.txt"
        f.write_text("hello")
        traversal = str(tmp_path) + "/../" + tmp_path.name + "/doc.txt"
        result = validate_file_path(traversal)
        assert result == f.resolve()

    def test_uppercase_extension_accepted(self, tmp_path: Path):
        f = tmp_path / "doc.PDF"
        f.write_bytes(b"%PDF fake")
        result = validate_file_path(str(f))
        assert result.name == "doc.PDF"

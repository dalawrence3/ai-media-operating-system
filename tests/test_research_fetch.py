"""Tests for HTTP fetch with security controls using httpx.MockTransport."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from app.research.constants import HTTP_MAX_BYTES
from app.research.errors import FetchError, SecurityError
from app.research.fetch import fetch_url


def _mock_client(
    responses: list[httpx.Response],
    *,
    follow_redirects: bool = True,
) -> httpx.Client:
    """Build an httpx.Client backed by a list of responses in sequence."""
    index = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        resp = responses[index["i"]]
        index["i"] += 1
        return resp

    transport = httpx.MockTransport(handler)
    return httpx.Client(transport=transport, follow_redirects=follow_redirects)


def _html_response(text: str = "<p>hello</p>", status: int = 200) -> httpx.Response:
    return httpx.Response(status, text=text, headers={"content-type": "text/html; charset=utf-8"})


def _text_response(text: str = "hello world") -> httpx.Response:
    return httpx.Response(200, text=text, headers={"content-type": "text/plain"})


def _pdf_response(content: bytes = b"%PDF-1.4 fake") -> httpx.Response:
    return httpx.Response(200, content=content, headers={"content-type": "application/pdf"})


class TestFetchUrl:
    def _public_url(self):
        return "http://example.com/article"

    def _patched_fetch(self, url: str, client: httpx.Client) -> object:
        with patch("app.research.validate.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(None, None, None, None, ("93.184.216.34", 80))]
            return fetch_url(url, http_client=client)

    def test_fetch_html_200(self):
        client = _mock_client([_html_response("<p>Hello world</p>")])
        result = self._patched_fetch(self._public_url(), client)
        assert result.http_status == 200
        assert b"Hello world" in result.content
        assert result.mime_type == "text/html"

    def test_fetch_text_200(self):
        client = _mock_client([_text_response("plain text content")])
        result = self._patched_fetch(self._public_url(), client)
        assert result.http_status == 200

    def test_fetch_pdf_200(self):
        client = _mock_client([_pdf_response(b"%PDF-1.4 content")])
        result = self._patched_fetch(self._public_url(), client)
        assert result.http_status == 200
        assert result.mime_type == "application/pdf"

    def test_fetch_404_raises_fetch_error(self):
        client = _mock_client([_html_response(status=404)])
        with pytest.raises(FetchError) as exc_info:
            self._patched_fetch(self._public_url(), client)
        assert exc_info.value.http_status == 404

    def test_fetch_403_raises_fetch_error(self):
        client = _mock_client([_html_response(status=403)])
        with pytest.raises(FetchError) as exc_info:
            self._patched_fetch(self._public_url(), client)
        assert exc_info.value.http_status == 403

    def test_fetch_500_raises_fetch_error(self):
        client = _mock_client([_html_response(status=500)])
        with pytest.raises(FetchError) as exc_info:
            self._patched_fetch(self._public_url(), client)
        assert exc_info.value.http_status == 500

    def test_fetch_timeout_raises_fetch_error(self):
        def timeout_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out", request=request)

        transport = httpx.MockTransport(timeout_handler)
        client = httpx.Client(transport=transport)
        with patch("app.research.validate.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(None, None, None, None, ("93.184.216.34", 80))]
            with pytest.raises(FetchError, match="timed out"):
                fetch_url(self._public_url(), http_client=client)

    def test_fetch_oversized_raises_fetch_error(self):
        big_content = b"x" * (HTTP_MAX_BYTES + 1)

        def big_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=big_content,
                headers={"content-type": "text/html"},
            )

        transport = httpx.MockTransport(big_handler)
        client = httpx.Client(transport=transport)
        with patch("app.research.validate.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(None, None, None, None, ("93.184.216.34", 80))]
            with pytest.raises(FetchError, match="MB"):
                fetch_url(self._public_url(), http_client=client)

    def test_fetch_wrong_mime_raises_fetch_error(self):
        def json_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=b'{"key":"val"}',
                headers={"content-type": "application/json"},
            )

        transport = httpx.MockTransport(json_handler)
        client = httpx.Client(transport=transport)
        with patch("app.research.validate.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(None, None, None, None, ("93.184.216.34", 80))]
            with pytest.raises(FetchError, match="content type"):
                fetch_url(self._public_url(), http_client=client)

    def test_fetch_stores_canonical_url_after_redirect(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "final" not in str(request.url):
                resp = httpx.Response(
                    301,
                    headers={
                        "location": "http://example.com/final",
                        "content-type": "text/html",
                    },
                )
            else:
                resp = _html_response("<p>final</p>")
            return resp

        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport, follow_redirects=True)
        with patch("app.research.validate.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(None, None, None, None, ("93.184.216.34", 80))]
            result = fetch_url("http://example.com/source", http_client=client)
        assert "example.com" in result.canonical_url

    def test_https_to_http_redirect_blocked(self):
        # First request (HTTPS) → 301 to http://; second (HTTP) → 200.
        # _check_https_downgrade should detect the downgrade in response.history.
        def downgrade_handler(request: httpx.Request) -> httpx.Response:
            if request.url.scheme == "https":
                return httpx.Response(
                    301,
                    headers={
                        "location": "http://example.com/insecure",
                        "content-type": "text/html",
                    },
                )
            return _html_response("<p>insecure landing</p>")

        transport = httpx.MockTransport(downgrade_handler)
        client = httpx.Client(transport=transport, follow_redirects=True)
        with patch("app.research.validate.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(None, None, None, None, ("93.184.216.34", 443))]
            with pytest.raises(SecurityError, match="HTTPS"):
                fetch_url("https://example.com/secure", http_client=client)

    def test_ssrf_url_never_reaches_network(self):
        # validate_url raises SecurityError before http_client is used
        called = [False]

        def tracking_handler(request: httpx.Request) -> httpx.Response:
            called[0] = True
            return _html_response()

        transport = httpx.MockTransport(tracking_handler)
        client = httpx.Client(transport=transport)
        with pytest.raises(SecurityError):
            fetch_url("http://192.168.1.1/page", http_client=client)
        assert called[0] is False

    def test_invalid_scheme_rejected_before_network(self):
        called = [False]

        def tracking_handler(request: httpx.Request) -> httpx.Response:
            called[0] = True
            return _html_response()

        transport = httpx.MockTransport(tracking_handler)
        client = httpx.Client(transport=transport)
        with pytest.raises(SecurityError, match="scheme"):
            fetch_url("ftp://example.com/file", http_client=client)
        assert called[0] is False

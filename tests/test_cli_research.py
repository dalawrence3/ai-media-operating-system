"""End-to-end CLI tests for Phase 4.1 research commands.

All HTTP goes through httpx.MockTransport — no live network.
All files use tmp_path — no real filesystem side-effects.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from typer.testing import CliRunner

from app.cli import app
from app.core.config import reset_config

runner = CliRunner()

_FETCH_CMD = ["sources", "fetch"]
_INGEST_CMD = ["sources", "ingest-file"]
_QUALITY_CMD = ["sources", "quality"]
_LONG_TEXT = b"word " * 400


@pytest.fixture(autouse=True)
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACE_DB_PATH", str(tmp_path / "test.db"))
    reset_config()
    yield
    reset_config()


def _create_topic(title: str = "Test Topic") -> None:
    result = runner.invoke(app, ["topics", "add", title])
    assert result.exit_code == 0


def _public_dns_patch():
    return patch(
        "app.research.validate.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("93.184.216.34", 80))],
    )


_FETCH_CLIENT_PATH = "app.research.fetch.httpx.Client"


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def _http_and_dns_patch(handler):
    """Context manager that patches both the http client and DNS resolution."""
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(
        patch(_FETCH_CLIENT_PATH, return_value=_mock_client(handler))
    )
    stack.enter_context(
        patch(
            "app.research.validate.socket.getaddrinfo",
            return_value=[(None, None, None, None, ("93.184.216.34", 80))],
        )
    )
    return stack


def _html_handler(body: bytes = _LONG_TEXT):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"<html><body>" + body + b"</body></html>",
            headers={"content-type": "text/html"},
        )

    return handler


def _status_handler(status: int):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            content=b"Error",
            headers={"content-type": "text/html"},
        )

    return handler


def _fetch(url: str, topic: int = 1, extra: list | None = None) -> object:
    args = [*_FETCH_CMD, url, "--topic", str(topic), *(extra or [])]
    return runner.invoke(app, args)


def _ingest(path: str, topic: int = 1, extra: list | None = None) -> object:
    args = [*_INGEST_CMD, path, "--topic", str(topic), *(extra or [])]
    return runner.invoke(app, args)


# ---------------------------------------------------------------------------
# sources fetch
# ---------------------------------------------------------------------------


class TestSourcesFetch:
    def test_fetch_html_success(self):
        _create_topic()
        with _http_and_dns_patch(_html_handler()):
            result = _fetch("http://example.com/article")
        assert result.exit_code == 0

    def test_fetch_missing_topic_exits_1(self):
        with _public_dns_patch():
            result = _fetch("http://example.com/", topic=999)
        assert result.exit_code == 1
        output = result.output
        assert "not found" in output

    def test_fetch_ssrf_url_exits_1_no_source_created(self):
        _create_topic()
        result = _fetch("http://192.168.1.1/")
        assert result.exit_code == 1
        list_result = runner.invoke(app, ["sources", "list", "1"])
        assert "No sources" in list_result.output or list_result.output.strip() == ""

    def test_fetch_invalid_scheme_exits_1(self):
        _create_topic()
        result = _fetch("ftp://example.com/file")
        assert result.exit_code == 1

    def test_fetch_404_exits_1(self):
        _create_topic()
        with _http_and_dns_patch(_status_handler(404)):
            result = _fetch("http://example.com/gone")
        assert result.exit_code == 1

    def test_fetch_idempotent_same_content_skipped(self):
        _create_topic()
        handler = _html_handler()
        with _http_and_dns_patch(handler):
            runner.invoke(app, [*_FETCH_CMD, "http://example.com/page", "--topic", "1"])
        with _http_and_dns_patch(handler):
            result = runner.invoke(
                app, [*_FETCH_CMD, "http://example.com/page", "--topic", "1"]
            )
        assert result.exit_code == 0
        assert "unchanged" in result.output

    def test_fetch_force_refetches(self):
        _create_topic()
        handler = _html_handler()
        with _http_and_dns_patch(handler):
            runner.invoke(app, [*_FETCH_CMD, "http://example.com/page", "--topic", "1"])
        with _http_and_dns_patch(handler):
            result = runner.invoke(
                app,
                [*_FETCH_CMD, "http://example.com/page", "--topic", "1", "--force"],
            )
        assert result.exit_code == 0
        assert "unchanged" not in result.output


# ---------------------------------------------------------------------------
# sources ingest-file
# ---------------------------------------------------------------------------


class TestSourcesIngestFile:
    def test_ingest_txt_success(self, tmp_path: Path):
        _create_topic()
        f = tmp_path / "doc.txt"
        f.write_text("word " * 400)
        result = _ingest(str(f))
        assert result.exit_code == 0
        assert "ingested" in result.output

    def test_ingest_md_success(self, tmp_path: Path):
        _create_topic()
        f = tmp_path / "doc.md"
        f.write_text("# Title\n\n" + "word " * 400)
        result = _ingest(str(f))
        assert result.exit_code == 0

    def test_ingest_missing_topic_exits_1(self, tmp_path: Path):
        f = tmp_path / "doc.txt"
        f.write_text("hello")
        result = _ingest(str(f), topic=999)
        assert result.exit_code == 1

    def test_ingest_missing_file_exits_1(self):
        _create_topic()
        result = _ingest("/nonexistent/file.txt")
        assert result.exit_code == 1

    def test_ingest_unsupported_extension_exits_1(self, tmp_path: Path):
        _create_topic()
        f = tmp_path / "doc.docx"
        f.write_bytes(b"fake docx content")
        result = _ingest(str(f))
        assert result.exit_code == 1

    def test_ingest_idempotent_same_content_skipped(self, tmp_path: Path):
        _create_topic()
        f = tmp_path / "doc.txt"
        f.write_text("word " * 400)
        _ingest(str(f))
        result = _ingest(str(f))
        assert result.exit_code == 0
        assert "unchanged" in result.output

    def test_ingest_force_reingest(self, tmp_path: Path):
        _create_topic()
        f = tmp_path / "doc.txt"
        f.write_text("word " * 400)
        _ingest(str(f))
        result = _ingest(str(f), extra=["--force"])
        assert result.exit_code == 0
        assert "unchanged" not in result.output

    def test_ingest_null_byte_in_path_exits_1(self):
        _create_topic()
        result = runner.invoke(app, [*_INGEST_CMD, "/tmp/file\x00.txt", "--topic", "1"])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# sources quality
# ---------------------------------------------------------------------------


class TestSourcesQuality:
    def test_quality_missing_source_exits_1(self):
        _create_topic()
        result = runner.invoke(app, [*_QUALITY_CMD, "999"])
        assert result.exit_code == 1
        output = result.output
        assert "not found" in output

    def test_quality_source_without_content_exits_1(self):
        _create_topic()
        runner.invoke(app, ["sources", "add", "http://example.com", "--topic", "1"])
        result = runner.invoke(app, [*_QUALITY_CMD, "1"])
        assert result.exit_code == 1

    def test_quality_displays_after_successful_ingest(self, tmp_path: Path):
        _create_topic()
        f = tmp_path / "doc.txt"
        f.write_text("word " * 400)
        _ingest(str(f))
        result = runner.invoke(app, [*_QUALITY_CMD, "1"])
        assert result.exit_code == 0
        assert "Composite" in result.output

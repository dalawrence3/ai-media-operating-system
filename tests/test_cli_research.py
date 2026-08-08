"""End-to-end CLI tests for Phase 4.1/4.2 research commands.

All HTTP goes through httpx.MockTransport — no live network.
All files use tmp_path — no real filesystem side-effects.
"""

from __future__ import annotations

import json as _json
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
    stack.enter_context(patch(_FETCH_CLIENT_PATH, return_value=_mock_client(handler)))
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
            result = runner.invoke(app, [*_FETCH_CMD, "http://example.com/page", "--topic", "1"])
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


# ---------------------------------------------------------------------------
# Phase 4.2 — sources extract-claims / list-claims / claim-runs
# ---------------------------------------------------------------------------


def _ingest_and_extract(tmp_path: Path, monkeypatch, *, text: str = "The sky is blue.") -> None:
    """Ingest a file and run extract-claims with the fake provider."""
    monkeypatch.setenv("ACE_AI_PROVIDER", "fake")
    from app.core.config import reset_config as _reset

    _reset()

    f = tmp_path / "doc.txt"
    f.write_text(text)
    _create_topic()
    res = _ingest(str(f))
    assert res.exit_code == 0

    fake_out = _json.dumps(
        {
            "claims": [
                {
                    "claim_text": "The sky is blue.",
                    "claim_type": "factual",
                    "supporting_quote": "sky is blue",
                }
            ]
        }
    )
    monkeypatch.setattr(
        "app.ai.fake.FakeProvider.__init__",
        lambda self, output=fake_out: setattr(self, "_output", output),
    )
    from unittest.mock import patch as _patch

    with _patch("app.ai.fake.FakeProvider._output", fake_out, create=True):
        pass  # just set up env; FakeProvider is instantiated in the CLI


def _extract_claims_cmd(source_id: int = 1, extra: list | None = None) -> object:
    args = ["sources", "extract-claims", str(source_id), *(extra or [])]
    return runner.invoke(app, args)


class TestSourcesExtractClaims:
    def test_missing_source_exits_1(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ACE_AI_PROVIDER", "fake")
        result = _extract_claims_cmd(999)
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_source_without_content_exits_1(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ACE_AI_PROVIDER", "fake")
        _create_topic()
        runner.invoke(app, ["sources", "add", "http://example.com", "--topic", "1"])
        result = _extract_claims_cmd(1)
        assert result.exit_code == 1

    def test_extract_succeeds_with_fake_provider(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("ACE_AI_PROVIDER", "fake")
        monkeypatch.setenv("ACE_DRY_RUN", "1")
        from app.core.config import reset_config as _reset

        _reset()

        _create_topic()
        f = tmp_path / "doc.txt"
        f.write_text("The sky is blue.")
        res = _ingest(str(f))
        assert res.exit_code == 0

        result = _extract_claims_cmd(1)
        assert result.exit_code == 0
        assert "Run" in result.output

    def test_extract_missing_source_shows_error(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ACE_AI_PROVIDER", "fake")
        result = runner.invoke(app, ["sources", "extract-claims", "9999"])
        assert result.exit_code == 1
        assert "not found" in result.output


class TestSourcesListClaims:
    def test_list_claims_empty_topic(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ACE_AI_PROVIDER", "fake")
        _create_topic()
        result = runner.invoke(app, ["sources", "list-claims", "1"])
        assert result.exit_code == 0
        assert "0 active evidence" in result.output

    def test_list_claims_after_extraction(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ACE_AI_PROVIDER", "fake")
        monkeypatch.setenv("ACE_DRY_RUN", "1")
        from app.core.config import reset_config as _reset

        _reset()

        _create_topic()
        f = tmp_path / "doc.txt"
        f.write_text("The sky is blue.")
        res = _ingest(str(f))
        assert res.exit_code == 0

        runner.invoke(app, ["sources", "extract-claims", "1"])
        result = runner.invoke(app, ["sources", "list-claims", "1"])
        assert result.exit_code == 0


class TestSourcesClaimRuns:
    def test_claim_runs_no_source_exits_1(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ACE_AI_PROVIDER", "fake")
        result = runner.invoke(app, ["sources", "claim-runs", "9999"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_claim_runs_no_content_returns_message(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ACE_AI_PROVIDER", "fake")
        _create_topic()
        runner.invoke(app, ["sources", "add", "1", "url", "http://example.com"])
        result = runner.invoke(app, ["sources", "claim-runs", "1"])
        assert result.exit_code == 0
        assert "no content" in result.output

    def test_claim_runs_shows_run_history(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ACE_AI_PROVIDER", "fake")
        monkeypatch.setenv("ACE_DRY_RUN", "1")
        from app.core.config import reset_config as _reset

        _reset()

        _create_topic()
        f = tmp_path / "doc.txt"
        f.write_text("The sky is blue.")
        res = _ingest(str(f))
        assert res.exit_code == 0

        runner.invoke(app, ["sources", "extract-claims", "1"])
        result = runner.invoke(app, ["sources", "claim-runs", "1"])
        assert result.exit_code == 0
        assert "Status" in result.output

"""Issue reporting: log excerpt, local summarizer, and /api/issues endpoints."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from reacher import diagnostics
from reacher.diagnostics.excerpt import EXCERPT_MAX_CHARS, build_excerpt
from reacher.issues.summarize import _parse_model_json, _sanitize, summarize_report


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("REACHER_LOG_DIR", str(tmp_path / "runs"))
    monkeypatch.delenv("REACHER_LLM_BIN", raising=False)
    monkeypatch.delenv("REACHER_LLM_MODEL", raising=False)
    monkeypatch.delenv("REACHER_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("REACHER_GITHUB_OWNER", raising=False)
    diagnostics.reset_for_tests()

    from fastapi.testclient import TestClient

    from reacher.api.app import create_app
    from reacher.api.middleware.auth import API_KEY

    with TestClient(create_app()) as client:
        client.headers.update({"Authorization": f"Bearer {API_KEY}"})
        yield client
    diagnostics.reset_for_tests()


def _write_run(tmp_path, records, meta=None):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    with open(run_dir / "app.ndjson", "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
    if meta is not None:
        (run_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return str(run_dir)


class TestExcerpt:
    def test_includes_errors_ui_and_meta(self, tmp_path):
        run_dir = _write_run(
            tmp_path,
            [
                {"ts": "t1", "lvl": "info", "evt": "ui.click", "msg": "Start", "src": "Monitor"},
                {"ts": "t2", "lvl": "error", "evt": "http.error", "msg": "boom", "src": "api"},
                {"ts": "t3", "lvl": "info", "evt": "session.state", "msg": "idle → connected"},
                {"ts": "t4", "lvl": "debug", "evt": "serial.tx", "msg": "ignored wire"},
            ],
            meta={"run_id": "abcd", "version": "3.4.0-alpha.2", "platform": "Linux"},
        )
        text = build_excerpt(run_dir)
        assert "run_id: abcd" in text
        assert "http.error" in text
        assert "ui.click" in text
        assert "idle → connected" in text
        assert "serial.tx" not in text

    def test_redacts_secret_keys_in_data(self, tmp_path):
        run_dir = _write_run(
            tmp_path,
            [
                {
                    "ts": "t1",
                    "lvl": "error",
                    "evt": "auth.fail",
                    "data": {"api_key": "super-secret", "port": "COM3"},
                }
            ],
        )
        text = build_excerpt(run_dir)
        assert "super-secret" not in text
        assert "[redacted]" in text
        assert "COM3" in text

    def test_hard_cap(self, tmp_path):
        records = [{"ts": f"t{i}", "lvl": "error", "evt": "x", "msg": "n" * 200} for i in range(200)]
        run_dir = _write_run(tmp_path, records)
        text = build_excerpt(run_dir, max_chars=2_000)
        assert len(text) <= 2_000
        assert "truncated" in text

    def test_empty_run(self, tmp_path):
        run_dir = tmp_path / "empty"
        run_dir.mkdir()
        assert "no diagnostic records" in build_excerpt(str(run_dir))


class TestSummarizeParse:
    def test_strips_fences_and_allowlists_labels(self):
        raw = """```json
{"title": "Cue tone never fires on START", "body": "## Description\\nCue 1 is armed but silent.", "labels": ["bug", "hardware", "invented"]}
```"""
        parsed = _parse_model_json(raw)
        out = _sanitize(parsed, excerpt="err line", versions="reacher 1", summarized=True)
        assert out["title"].startswith("Cue tone")
        assert out["labels"] == ["bug", "hardware"]
        assert "## Diagnostic excerpt" in out["body"]
        assert out["summarized"] is True

    def test_fallback_on_garbage(self, monkeypatch, tmp_path):
        monkeypatch.setenv("REACHER_LLM_BIN", str(tmp_path / "llama-cli"))
        monkeypatch.setenv("REACHER_LLM_MODEL", str(tmp_path / "model.gguf"))
        (tmp_path / "llama-cli").write_text("x")
        (tmp_path / "model.gguf").write_text("x")

        def boom(_user):
            raise RuntimeError("llama exploded")

        monkeypatch.setattr("reacher.issues.summarize._run_llama", boom)
        out = summarize_report(
            repo="labrynth",
            description="The pump never fired",
            excerpt="error | pump",
            severity="critical",
        )
        assert out["summarized"] is False
        assert "needs-triage" in out["labels"]
        assert "The pump never fired" in out["body"]


class TestIssuesApi:
    def test_status_unconfigured(self, api):
        body = api.get("/api/issues/status").json()
        assert body["llm"] is False
        assert body["github"] is False
        assert body["owner"] == "Otis-Lab-MUSC"
        assert "labrynth" in body["repos"]

    def test_status_configured(self, api, tmp_path, monkeypatch):
        bin_path = tmp_path / "llama-cli"
        model = tmp_path / "model.gguf"
        bin_path.write_text("x")
        model.write_text("x")
        monkeypatch.setenv("REACHER_LLM_BIN", str(bin_path))
        monkeypatch.setenv("REACHER_LLM_MODEL", str(model))
        monkeypatch.setenv("REACHER_GITHUB_TOKEN", "ghp_test")
        monkeypatch.setenv("REACHER_GITHUB_OWNER", "example-org")
        body = api.get("/api/issues/status").json()
        assert body["llm"] is True
        assert body["github"] is True
        assert body["owner"] == "example-org"

    def test_report_requires_auth(self, api):
        res = api.post(
            "/api/issues/report",
            json={"description": "something broke"},
            headers={"Authorization": ""},
        )
        assert res.status_code == 401

    def test_report_503_without_llm(self, api):
        res = api.post("/api/issues/report", json={"description": "something broke"})
        assert res.status_code == 503
        assert "summarizer" in res.json()["detail"].lower()

    def test_report_summarize_without_github(self, api, tmp_path, monkeypatch):
        bin_path = tmp_path / "llama-cli"
        model = tmp_path / "model.gguf"
        bin_path.write_text("x")
        model.write_text("x")
        monkeypatch.setenv("REACHER_LLM_BIN", str(bin_path))
        monkeypatch.setenv("REACHER_LLM_MODEL", str(model))

        fake = {
            "title": "Pump relay never closes",
            "body": "## Description\nPump 1 armed but silent.",
            "labels": ["bug", "hardware"],
            "summarized": True,
        }
        with patch("reacher.api.routers.issues.summarize_report", return_value=fake):
            res = api.post(
                "/api/issues/report",
                json={"description": "pump did nothing", "severity": "moderate"},
            )
        assert res.status_code == 200
        data = res.json()
        assert data["filed"] is False
        assert data["html_url"] is None
        assert data["title"] == fake["title"]
        assert data["summarized"] is True

    def test_report_files_when_token_set(self, api, tmp_path, monkeypatch):
        bin_path = tmp_path / "llama-cli"
        model = tmp_path / "model.gguf"
        bin_path.write_text("x")
        model.write_text("x")
        monkeypatch.setenv("REACHER_LLM_BIN", str(bin_path))
        monkeypatch.setenv("REACHER_LLM_MODEL", str(model))
        monkeypatch.setenv("REACHER_GITHUB_TOKEN", "ghp_test")

        fake = {
            "title": "Cue tone never fires",
            "body": "## Description\nSilent cue.",
            "labels": ["bug"],
            "summarized": True,
        }
        with (
            patch("reacher.api.routers.issues.summarize_report", return_value=fake),
            patch(
                "reacher.api.routers.issues.create_issue",
                new_callable=AsyncMock,
                return_value="https://github.com/Otis-Lab-MUSC/labrynth/issues/1",
            ) as created,
        ):
            res = api.post(
                "/api/issues/report",
                json={"description": "no sound", "repo": "labrynth", "app_version": "3.0.1-alpha.14"},
            )
        assert res.status_code == 200
        data = res.json()
        assert data["filed"] is True
        assert data["html_url"].endswith("/issues/1")
        created.assert_awaited_once()
        kwargs = created.await_args.kwargs
        assert kwargs["repo"] == "labrynth"
        assert kwargs["summarized"] is True

    def test_fallback_path_passes_summarized_false(self, api, tmp_path, monkeypatch):
        bin_path = tmp_path / "llama-cli"
        model = tmp_path / "model.gguf"
        bin_path.write_text("x")
        model.write_text("x")
        monkeypatch.setenv("REACHER_LLM_BIN", str(bin_path))
        monkeypatch.setenv("REACHER_LLM_MODEL", str(model))
        monkeypatch.setenv("REACHER_GITHUB_TOKEN", "ghp_test")

        fake = {
            "title": "The pump never fired",
            "body": "## Description\nThe pump never fired",
            "labels": ["bug", "needs-triage"],
            "summarized": False,
        }
        with (
            patch("reacher.api.routers.issues.summarize_report", return_value=fake),
            patch(
                "reacher.api.routers.issues.create_issue",
                new_callable=AsyncMock,
                return_value="https://github.com/Otis-Lab-MUSC/labrynth/issues/2",
            ) as created,
        ):
            res = api.post("/api/issues/report", json={"description": "The pump never fired"})
        assert res.status_code == 200
        assert res.json()["summarized"] is False
        assert created.await_args.kwargs["summarized"] is False


def test_excerpt_max_constant_fits_github():
    assert EXCERPT_MAX_CHARS < 65_536

"""Issue reporting: log excerpt, local summarizer, and /api/issues endpoints."""

from __future__ import annotations

import json
import sys
from unittest.mock import AsyncMock, patch

import pytest

from reacher import diagnostics
from reacher.diagnostics.excerpt import EXCERPT_MAX_CHARS, build_excerpt
from reacher.issues.summarize import (
    _llama_argv,
    _parse_model_json,
    _sanitize,
    llm_probe,
    reset_probe_cache,
    summarize_report,
)


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("REACHER_LOG_DIR", str(tmp_path / "runs"))
    monkeypatch.delenv("REACHER_LLM_BIN", raising=False)
    monkeypatch.delenv("REACHER_LLM_MODEL", raising=False)
    monkeypatch.delenv("REACHER_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("REACHER_GITHUB_OWNER", raising=False)
    diagnostics.reset_for_tests()
    reset_probe_cache()

    from fastapi.testclient import TestClient

    from reacher.api.app import create_app
    from reacher.api.middleware.auth import API_KEY

    with TestClient(create_app()) as client:
        client.headers.update({"Authorization": f"Bearer {API_KEY}"})
        yield client
    diagnostics.reset_for_tests()
    reset_probe_cache()


def _stub_llama(path, body="raise SystemExit(0)"):
    """Write an executable llama.cpp stand-in.

    The liveness probe actually runs the binary, so a text file no longer
    passes — which is the whole point of the probe.
    """
    path.write_text(f"#!{sys.executable}\nimport sys\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def fake_llm(tmp_path, monkeypatch):
    """Configure a runnable summarizer; yields (bin, model) for further tweaking."""
    bin_path = _stub_llama(tmp_path / "llama-completion")
    model = tmp_path / "model.gguf"
    model.write_text("x")
    monkeypatch.setenv("REACHER_LLM_BIN", str(bin_path))
    monkeypatch.setenv("REACHER_LLM_MODEL", str(model))
    reset_probe_cache()
    yield bin_path, model
    reset_probe_cache()


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

    def test_status_configured(self, api, fake_llm, monkeypatch):
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

    def test_report_summarize_without_github(self, api, fake_llm):
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

    def test_report_files_when_token_set(self, api, fake_llm, monkeypatch):
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

    def test_fallback_path_passes_summarized_false(self, api, fake_llm, monkeypatch):
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


class TestLlmProbe:
    """The probe exists because two separate defects shipped looking healthy."""

    def test_unset_env_reports_why(self, monkeypatch):
        monkeypatch.delenv("REACHER_LLM_BIN", raising=False)
        monkeypatch.delenv("REACHER_LLM_MODEL", raising=False)
        reset_probe_cache()
        status = llm_probe()
        assert status.ok is False
        assert "not set" in status.detail

    def test_missing_model_reports_path(self, tmp_path, monkeypatch):
        bin_path = _stub_llama(tmp_path / "llama-completion")
        monkeypatch.setenv("REACHER_LLM_BIN", str(bin_path))
        monkeypatch.setenv("REACHER_LLM_MODEL", str(tmp_path / "absent.gguf"))
        reset_probe_cache()
        status = llm_probe()
        assert status.ok is False
        assert "GGUF model not found" in status.detail

    def test_non_executable_binary_fails(self, tmp_path, monkeypatch):
        """Regression: a bundle missing its shared libraries is not 'available'.

        Shipped as a plain unreadable/unrunnable file, this used to pass the
        old ``os.path.isfile`` check and only fail when a user filed a report.
        """
        bin_path = tmp_path / "llama-completion"
        bin_path.write_text("not an executable")
        bin_path.chmod(0o644)
        (tmp_path / "model.gguf").write_text("x")
        monkeypatch.setenv("REACHER_LLM_BIN", str(bin_path))
        monkeypatch.setenv("REACHER_LLM_MODEL", str(tmp_path / "model.gguf"))
        reset_probe_cache()
        status = llm_probe()
        assert status.ok is False
        assert "llama-completion" in status.detail

    def test_binary_rejecting_our_flags_fails(self, tmp_path, monkeypatch):
        """Regression: llama-cli b10622 dropped --no-conversation.

        The binary starts and ``--version`` succeeds, so only a probe that
        sends the *real* argv catches it.
        """
        bin_path = _stub_llama(
            tmp_path / "llama-cli",
            body=(
                "if '--no-conversation' in sys.argv:\n"
                "    sys.stderr.write('error: invalid argument: --no-conversation')\n"
                "    raise SystemExit(1)\n"
                "raise SystemExit(0)"
            ),
        )
        (tmp_path / "model.gguf").write_text("x")
        monkeypatch.setenv("REACHER_LLM_BIN", str(bin_path))
        monkeypatch.setenv("REACHER_LLM_MODEL", str(tmp_path / "model.gguf"))
        reset_probe_cache()
        status = llm_probe()
        assert status.ok is False
        assert "invalid argument" in status.detail

    def test_probe_sends_a_non_empty_prompt(self, tmp_path, monkeypatch):
        """Regression: llama.cpp exits non-zero on an empty prompt.

        A probe that passes ``-p ""`` reports a perfectly good bundle as
        broken — a false negative is as bad as the bug it screens for.
        """
        bin_path = _stub_llama(
            tmp_path / "llama-completion",
            body=(
                "i = sys.argv.index('-p')\n"
                "if not sys.argv[i + 1]:\n"
                "    sys.stderr.write('input is empty')\n"
                "    raise SystemExit(255)\n"
                "raise SystemExit(0)"
            ),
        )
        (tmp_path / "model.gguf").write_text("x")
        monkeypatch.setenv("REACHER_LLM_BIN", str(bin_path))
        monkeypatch.setenv("REACHER_LLM_MODEL", str(tmp_path / "model.gguf"))
        reset_probe_cache()
        status = llm_probe()
        assert status.ok is True, status.detail

    def test_probe_argv_matches_real_argv(self):
        """The probe is worthless if it can drift from what inference runs."""
        probe = _llama_argv("/bin/llama", "/m.gguf", prompt="", n_predict=0)
        real = _llama_argv("/bin/llama", "/m.gguf", prompt_file="/p.txt", n_predict=1024)
        flags = lambda argv: [a for a in argv if a.startswith("--") or a.startswith("-")]  # noqa: E731
        assert set(flags(probe)) - {"-p"} == set(flags(real)) - {"-f"}
        assert "--no-conversation" in probe
        assert probe[probe.index("-n") + 1] == "0"

    def test_result_is_cached_then_invalidated(self, fake_llm):
        bin_path, _model = fake_llm
        assert llm_probe().ok is True
        # Break the binary; the cached result stands until the file changes.
        cached = llm_probe()
        assert cached.ok is True
        _stub_llama(bin_path, body="raise SystemExit(1)")
        assert llm_probe().ok is False


class TestStatusSurfacesReason:
    def test_status_reports_detail_when_unavailable(self, api):
        body = api.get("/api/issues/status").json()
        assert body["llm"] is False
        assert body["llm_detail"]

    def test_status_detail_is_null_when_healthy(self, api, fake_llm):
        body = api.get("/api/issues/status").json()
        assert body["llm"] is True
        assert body["llm_detail"] is None

    def test_report_503_explains_the_failure(self, api, tmp_path, monkeypatch):
        bin_path = tmp_path / "llama-completion"
        bin_path.write_text("broken")
        bin_path.chmod(0o644)
        (tmp_path / "model.gguf").write_text("x")
        monkeypatch.setenv("REACHER_LLM_BIN", str(bin_path))
        monkeypatch.setenv("REACHER_LLM_MODEL", str(tmp_path / "model.gguf"))
        reset_probe_cache()
        res = api.post("/api/issues/report", json={"description": "broke"})
        assert res.status_code == 503
        detail = res.json()["detail"]
        assert "not available" in detail
        assert "llama-completion" in detail

    def test_fallback_response_carries_error(self, api, fake_llm):
        fake = {
            "title": "t",
            "body": "b",
            "labels": ["bug"],
            "summarized": False,
            "error": "llama-completion exited 1: boom",
        }
        with patch("reacher.api.routers.issues.summarize_report", return_value=fake):
            res = api.post("/api/issues/report", json={"description": "x"})
        assert res.status_code == 200
        assert res.json()["summary_error"] == "llama-completion exited 1: boom"


def test_excerpt_max_constant_fits_github():
    assert EXCERPT_MAX_CHARS < 65_536

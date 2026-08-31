"""Issue reporting: log excerpt builder and POST /api/issues/prefill."""

from __future__ import annotations

import json
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest

from reacher import diagnostics
from reacher.diagnostics.excerpt import build_excerpt
from reacher.issues.prefill import URL_BUDGET, build_prefill, github_owner, issue_url


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("REACHER_LOG_DIR", str(tmp_path / "runs"))
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


class TestBuildPrefill:
    """No network, no subprocess — pure string composition."""

    def test_never_touches_network_or_subprocess(self, monkeypatch):
        with (
            patch("httpx.AsyncClient.post", side_effect=AssertionError("must not run")),
            patch("subprocess.run", side_effect=AssertionError("must not run")),
            patch("subprocess.Popen", side_effect=AssertionError("must not run")),
        ):
            result = build_prefill(repo="labrynth", description="The pump never fired")
        assert result["url"].startswith("https://github.com/")

    def test_title_from_first_line_of_description(self):
        result = build_prefill(repo="labrynth", description="Pump relay never closes.\nMore detail here.")
        assert result["title"] == "Pump relay never closes."

    def test_long_description_title_is_capped(self):
        result = build_prefill(repo="labrynth", description="x" * 200)
        assert len(result["title"]) <= 72
        assert result["title"].endswith("…")

    def test_body_includes_description_and_environment(self):
        result = build_prefill(repo="labrynth", description="The pump never fired", versions="reacher 9.9.9")
        assert "The pump never fired" in result["body"]
        assert "reacher 9.9.9" in result["body"]

    def test_body_has_repro_placeholder_when_steps_empty(self):
        result = build_prefill(repo="labrynth", description="x")
        assert "## Steps to Reproduce" in result["body"]
        assert "What were you doing" in result["body"]

    def test_body_uses_users_own_steps_when_given(self):
        result = build_prefill(repo="labrynth", description="x", steps="1. Click Start\n2. Watch nothing happen")
        assert "1. Click Start" in result["body"]
        assert "What were you doing" not in result["body"]

    def test_severity_included_when_set(self):
        result = build_prefill(repo="labrynth", description="x", severity="critical")
        assert "critical" in result["body"]

    def test_labels_are_filtered_to_allowlist_and_develop_is_added(self):
        result = build_prefill(repo="labrynth", description="x", labels=["bug", "not-a-label", "hardware"])
        assert result["labels"] == ["bug", "hardware", "develop"]

    def test_owner_defaults_to_otis_lab(self, monkeypatch):
        monkeypatch.delenv("REACHER_GITHUB_OWNER", raising=False)
        assert github_owner() == "Otis-Lab-MUSC"
        assert build_prefill(repo="labrynth", description="x")["owner"] == "Otis-Lab-MUSC"

    def test_owner_env_override(self, monkeypatch):
        monkeypatch.setenv("REACHER_GITHUB_OWNER", "example-org")
        assert build_prefill(repo="labrynth", description="x")["url"].startswith(
            "https://github.com/example-org/labrynth/"
        )

    def test_url_stays_within_budget_even_with_a_huge_excerpt(self, tmp_path):
        records = [{"ts": f"t{i}", "lvl": "error", "evt": "x", "msg": "n" * 200} for i in range(500)]
        run_dir = _write_run(tmp_path, records)
        fake_excerpt = lambda max_chars=0: build_excerpt(run_dir, max_chars=max_chars)  # noqa: E731
        with patch("reacher.issues.prefill.build_current_excerpt", side_effect=fake_excerpt):
            result = build_prefill(repo="labrynth", description="short report")
        assert len(result["url"]) <= URL_BUDGET

    def test_huge_description_still_produces_a_bounded_url(self):
        result = build_prefill(repo="labrynth", description="x" * 50_000, steps="y" * 50_000)
        assert len(result["url"]) <= URL_BUDGET + 500  # blunt fallback truncation, not exact
        assert "truncated" in result["body"]


class TestIssueUrl:
    def test_round_trips_through_query_params(self):
        url = issue_url("Otis-Lab-MUSC", "reacher", "A title", "A body\nwith a newline", ["bug", "develop"])
        parsed = urlparse(url)
        assert parsed.scheme == "https"
        assert parsed.netloc == "github.com"
        assert parsed.path == "/Otis-Lab-MUSC/reacher/issues/new"
        qs = parse_qs(parsed.query)
        assert qs["title"] == ["A title"]
        assert qs["body"] == ["A body\nwith a newline"]
        assert qs["labels"] == ["bug,develop"]

    def test_labels_param_omitted_when_empty(self):
        url = issue_url("Otis-Lab-MUSC", "reacher", "t", "b", [])
        assert "labels=" not in url


class TestPrefillEndpoint:
    def test_requires_auth(self, api):
        res = api.post(
            "/api/issues/prefill",
            json={"description": "something broke"},
            headers={"Authorization": ""},
        )
        assert res.status_code == 401

    def test_returns_a_prefilled_link(self, api):
        res = api.post(
            "/api/issues/prefill",
            json={"description": "The pump never fired", "severity": "moderate", "repo": "labrynth"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["repo"] == "labrynth"
        assert data["owner"] == "Otis-Lab-MUSC"
        assert data["url"].startswith("https://github.com/Otis-Lab-MUSC/labrynth/issues/new?")
        assert "develop" in data["labels"]

    def test_rejects_a_bad_repo(self, api):
        res = api.post("/api/issues/prefill", json={"description": "x", "repo": "evil"})
        assert res.status_code == 422

    def test_rejects_empty_description(self, api):
        res = api.post("/api/issues/prefill", json={"description": ""})
        assert res.status_code == 422

    def test_rejects_oversized_description(self, api):
        res = api.post("/api/issues/prefill", json={"description": "x" * 3000})
        assert res.status_code == 422

    def test_labels_are_allowlist_filtered_and_capped(self, api):
        res = api.post(
            "/api/issues/prefill",
            json={
                "description": "x",
                "labels": ["bug", "not-a-label", "agent-ready", "hardware", "UI", "camera"],
            },
        )
        data = res.json()
        # allowlisted only, capped at 3 category labels, plus routing label
        assert data["labels"] == ["bug", "hardware", "UI", "develop"]

    def test_app_version_is_folded_into_the_body(self, api):
        res = api.post(
            "/api/issues/prefill",
            json={"description": "x", "app_version": "3.0.1-alpha.14"},
        )
        assert "3.0.1-alpha.14" in res.json()["body"]

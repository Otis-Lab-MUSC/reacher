"""Integration tests for POST /api/validate/config."""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from reacher.api.app import create_app
from reacher.api.middleware.auth import API_KEY

AUTH = {"Authorization": f"Bearer {API_KEY}"}


@pytest.fixture
def client():
    with patch("reacher.session_manager.REACHER"), patch("os.makedirs"):
        app = create_app()
        with TestClient(app) as c:
            yield c


class TestValidateConfigAuth:
    def test_requires_auth(self, client):
        resp = client.post("/api/validate/config", json={})
        assert resp.status_code == 401


class TestValidateConfigDegradation:
    def test_returns_empty_when_ollama_unreachable(self, client):
        import httpx as _httpx

        with patch("reacher.api.routers.validate._call_ollama", side_effect=_httpx.ConnectError("refused")):
            resp = client.post("/api/validate/config", json={}, headers=AUTH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        assert body["warnings"] == []

    def test_returns_empty_on_timeout(self, client):
        with patch("reacher.api.routers.validate._call_ollama", side_effect=asyncio.TimeoutError()):
            resp = client.post("/api/validate/config", json={}, headers=AUTH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        assert body["warnings"] == []


class TestValidateConfigParsing:
    def test_parses_ollama_warnings(self, client):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "message": {
                "content": json.dumps({
                    "valid": False,
                    "warnings": [{"field": "pump", "message": "No pump armed", "severity": "error"}],
                    "suggestions": "Arm at least one pump.",
                })
            }
        }
        mock_response.raise_for_status = MagicMock()

        async def fake_call(config_json):
            from reacher.api.routers.validate import ValidateConfigResponse, ValidationWarning
            return ValidateConfigResponse(
                valid=False,
                warnings=[ValidationWarning(field="pump", message="No pump armed", severity="error")],
                suggestions="Arm at least one pump.",
            )

        with patch("reacher.api.routers.validate._call_ollama", side_effect=fake_call):
            resp = client.post(
                "/api/validate/config",
                json={"paradigm": "fr", "hardwareUi": {"primaryPump": {"armed": False}}},
                headers=AUTH,
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False
        assert len(body["warnings"]) == 1
        assert body["warnings"][0]["severity"] == "error"
        assert body["suggestions"] == "Arm at least one pump."

    def test_empty_body_accepted(self, client):
        with patch("reacher.api.routers.validate._call_ollama", side_effect=Exception("offline")):
            resp = client.post("/api/validate/config", json={}, headers=AUTH)
        assert resp.status_code == 200

    def test_full_payload_accepted(self, client):
        payload = {
            "paradigm": "pavlovian",
            "paradigmSettings": None,
            "hardwareUi": {
                "primaryPump": {"armed": True, "timeout": 5000},
                "laser": {"armed": False},
            },
            "pavlovianParams": {"208": 10, "209": 10, "216": 5000, "217": 3000, "218": 8000, "213": 2000},
            "limitSettings": {"limitType": "Trials", "infusionLimit": 20},
        }
        with patch("reacher.api.routers.validate._call_ollama", side_effect=Exception("offline")):
            resp = client.post("/api/validate/config", json=payload, headers=AUTH)
        assert resp.status_code == 200

"""AI-assisted session configuration validation endpoint."""

import asyncio
import json
import logging
import os
from typing import Any, Optional

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from .validation_rules import SYSTEM_PROMPT

router = APIRouter()
logger = logging.getLogger(__name__)

OLLAMA_URL = os.getenv("REACHER_OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("REACHER_OLLAMA_MODEL", "qwen2.5:7b")


class ValidateConfigRequest(BaseModel):
    paradigm: Optional[str] = None
    paradigmSettings: Optional[dict[str, Any]] = None
    hardwareUi: Optional[dict[str, Any]] = None
    pavlovianParams: Optional[dict[str, Any]] = None
    limitSettings: Optional[dict[str, Any]] = None


class ValidationWarning(BaseModel):
    field: str
    message: str
    severity: str  # "warning" | "error"


class ValidateConfigResponse(BaseModel):
    valid: bool
    warnings: list[ValidationWarning]
    suggestions: str


_EMPTY = ValidateConfigResponse(valid=True, warnings=[], suggestions="")


async def _call_ollama(config_json: str) -> ValidateConfigResponse:
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": config_json},
        ],
        "stream": False,
        "format": "json",
    }
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=10.0)
        r.raise_for_status()
    raw = r.json()["message"]["content"]
    parsed = json.loads(raw)
    return ValidateConfigResponse(
        valid=parsed.get("valid", True),
        warnings=[ValidationWarning(**w) for w in parsed.get("warnings", [])],
        suggestions=parsed.get("suggestions", ""),
    )


@router.post("/config", response_model=ValidateConfigResponse)
async def validate_config(body: ValidateConfigRequest) -> ValidateConfigResponse:
    """Run AI-assisted validation on a session config before start.

    Returns empty warnings on any Ollama error so the session start is never blocked.
    """
    try:
        return await asyncio.wait_for(_call_ollama(body.model_dump_json()), timeout=10.0)
    except Exception:
        logger.debug("AI config validation unavailable — proceeding without warnings", exc_info=True)
        return _EMPTY

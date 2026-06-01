"""Session configuration validation endpoint."""

import logging

from fastapi import APIRouter

from .validators import ValidateConfigRequest, ValidateConfigResponse, _EMPTY, run_validation

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/config", response_model=ValidateConfigResponse)
async def validate_config(body: ValidateConfigRequest) -> ValidateConfigResponse:
    """Validate a session config before start.

    Returns empty warnings on any rule engine error so session start is never blocked.
    """
    try:
        result = run_validation(body)
        logger.info("Config validation complete: valid=%s warnings=%d", result.valid, len(result.warnings))
        return result
    except Exception:
        logger.exception("Config validation error — proceeding without warnings")
        return _EMPTY

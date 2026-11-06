"""Session configuration validation endpoint."""

import logging

from fastapi import APIRouter

from .validators import ValidateConfigRequest, ValidateConfigResponse, _EMPTY, run_validation

router = APIRouter()
logger = logging.getLogger(__name__)

# The rule engine reads client-supplied JSON typed ``dict[str, Any]``, so a malformed value fails by
# attribute access on a non-dict (AttributeError), a comparison or sum across types (TypeError), or a
# float sum overflowing (OverflowError), or an integer past Python's 4300-digit string limit when a
# rule formats it into a warning (ValueError).
_RULE_DATA_ERRORS: tuple[type[Exception], ...] = (AttributeError, TypeError, OverflowError, ValueError)


@router.post("/config", response_model=ValidateConfigResponse)
async def validate_config(body: ValidateConfigRequest) -> ValidateConfigResponse:
    """Validate a session config before start.

    Returns empty warnings when a rule cannot read the submitted values, so a malformed config never
    blocks session start.
    """
    try:
        result = run_validation(body)
        logger.info("Config validation complete: valid=%s warnings=%d", result.valid, len(result.warnings))
        return result
    except _RULE_DATA_ERRORS:
        logger.exception("Config validation error — proceeding without warnings")
        return _EMPTY

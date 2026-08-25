"""Structured diagnostic logging for REACHER.

One NDJSON stream per process run, carrying records from every layer — browser
UI, HTTP/WebSocket API, session kernel, and the raw serial wire — correlated by
``corr_id`` so a single button press can be traced through to the bytes that
reached the Arduino.

Typical use::

    from reacher.diagnostics import configure_logging, log

    configure_logging()                       # once, at process start
    log("serial.tx", tier="wire", line=payload, session_id=sid)
"""

from .context import RUN_ID, bind, get_corr_id, new_corr_id, set_session_id
from .schema import (
    TIER_API,
    TIER_APP,
    TIER_KERNEL,
    TIER_UI,
    TIER_WIRE,
    LogRecord,
)
from .setup import (
    configure_logging,
    get_sink,
    install_signal_handlers,
    log,
    reset_for_tests,
    uvicorn_log_config,
)

__all__ = [
    "RUN_ID",
    "bind",
    "get_corr_id",
    "new_corr_id",
    "set_session_id",
    "LogRecord",
    "TIER_APP",
    "TIER_API",
    "TIER_KERNEL",
    "TIER_WIRE",
    "TIER_UI",
    "configure_logging",
    "get_sink",
    "install_signal_handlers",
    "log",
    "reset_for_tests",
    "uvicorn_log_config",
]

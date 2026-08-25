"""Bridge from stdlib ``logging`` into the diagnostic sink.

This is where most of the value comes from: the codebase already has ~205
``logger.*`` calls across 23 modules, but no root handler was ever installed, so
in a shipped build every one of them was discarded.  Installing this single
handler on the root logger makes all of them durable without touching one call
site.

Records keep their module name in ``src`` and are tiered by that name, so a
reader can still tell kernel logging from API logging even though neither was
written with this system in mind.
"""

import logging
from typing import Optional

from .schema import (
    TIER_API,
    TIER_APP,
    TIER_KERNEL,
    LogRecord,
    level_name,
)

#: Attributes present on every stdlib record; anything else the caller passed via
#: ``extra=`` is genuinely interesting and gets carried into ``data``.
_STD_ATTRS = frozenset(
    """args asctime created exc_info exc_text filename funcName levelname levelno
    lineno module msecs message msg name pathname process processName relativeCreated
    stack_info thread threadName taskName""".split()
)


def _tier_for(name: str) -> str:
    """Infer a tier from the logger's module path."""
    if name.startswith("reacher.kernel") or name.startswith("reacher.session_manager"):
        return TIER_KERNEL
    if name.startswith("reacher.api") or name.startswith("uvicorn"):
        return TIER_API
    return TIER_APP


class SinkHandler(logging.Handler):
    """A ``logging.Handler`` that forwards records to a :class:`LogSink`."""

    def __init__(self, sink, level: int = logging.NOTSET):
        super().__init__(level=level)
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.sink.emit(self._convert(record))
        except Exception:
            # handleError writes to stderr and never re-enters logging.
            self.handleError(record)

    def _convert(self, record: logging.LogRecord) -> LogRecord:
        try:
            msg = record.getMessage()
        except Exception as exc:
            msg = f"<unformattable log message: {type(exc).__name__}: {exc}>"

        data = {}
        for key, value in record.__dict__.items():
            if key not in _STD_ATTRS and not key.startswith("_"):
                data[key] = value

        if record.exc_info:
            try:
                data["exc"] = self.format_exception(record.exc_info)
            except Exception:
                pass

        # A logger name is a better event identity than nothing, but an explicit
        # evt= passed through `extra` always wins.
        evt = data.pop("evt", None) or f"log.{record.levelname.lower()}"

        from .redact import redact

        # Pop the promoted fields *before* redacting, so they land in their
        # dedicated columns instead of being duplicated inside `data`.
        tier = data.pop("tier", None) or _tier_for(record.name)
        session_id = data.pop("session_id", None)
        corr_id = data.pop("corr_id", None)

        return LogRecord(
            evt=evt,
            tier=tier,
            lvl=level_name(record.levelno),
            msg=msg,
            src=record.name,
            data=redact(data) if data else {},
            session_id=session_id,
            corr_id=corr_id,
        )

    @staticmethod
    def format_exception(exc_info) -> str:
        import traceback

        return "".join(traceback.format_exception(*exc_info)).strip()


def install(sink, level: int = logging.INFO, root: Optional[logging.Logger] = None) -> SinkHandler:
    """Attach a :class:`SinkHandler` to the root logger.

    Idempotent: a second call replaces the existing handler rather than
    doubling every line, which matters because ``configure_logging`` is called
    from both ``main()`` and the FastAPI lifespan.
    """
    root = root or logging.getLogger()
    for existing in list(root.handlers):
        if isinstance(existing, SinkHandler):
            root.removeHandler(existing)
    handler = SinkHandler(sink)
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)
    return handler

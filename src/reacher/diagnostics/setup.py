"""Wiring: build the sink, capture stdlib logging, and catch every exit path.

``configure_logging()`` is the single entry point.  It is idempotent and safe to
call from ``main()``, from the FastAPI lifespan, and from tests, because the
process can be started four different ways (``python -m reacher``, the
``reacher`` console script, the frozen Labrynth launcher, and ``TestClient``).
"""

import atexit
import faulthandler
import json
import logging
import os
import signal
import sys
import threading
from typing import Optional

from . import context
from .bridge import install as install_bridge
from .schema import TIER_APP, LogRecord
from .sink import LogSink, prune_runs

#: Third-party loggers that are far too chatty at DEBUG to be useful here.
#: The goal is a complete record of *REACHER's* behaviour — the subnet-scan
#: fallback alone probes ~500 hosts a cycle, and httpcore logs every TCP dial,
#: which would bury the signal and churn through log rotation for nothing.
#: Raising their floor keeps warnings and errors from these libraries while
#: dropping their routine chatter.
QUIET_LOGGERS = {
    "httpcore": logging.WARNING,
    "httpx": logging.WARNING,
    "hpack": logging.WARNING,
    "h11": logging.WARNING,
    "asyncio": logging.INFO,
    "zeroconf": logging.INFO,
    "urllib3": logging.WARNING,
    "multipart": logging.INFO,
    "python_multipart": logging.INFO,
    "websockets": logging.INFO,
    "PIL": logging.INFO,
    "matplotlib": logging.WARNING,
}

_sink: Optional[LogSink] = None
_configured = False
_lock = threading.Lock()
_crash_fh = None
_prev_signal_handlers: dict = {}


def get_sink() -> Optional[LogSink]:
    """Return the active sink, or None if logging was never configured."""
    return _sink


def log(
    evt: str,
    tier: str = TIER_APP,
    lvl: str = "info",
    msg: str = "",
    src: str = "",
    session_id: Optional[str] = None,
    **data,
) -> None:
    """Emit a structured record directly, bypassing stdlib formatting.

    Preferred over ``logger.info`` for high-volume machine-readable events (the
    serial wire, WebSocket fanout) where a human-readable message string would
    be wasted work on the hot path.  A no-op when logging is unconfigured, so
    instrumentation can be added anywhere without an import-order dependency.
    """
    if _sink is None:
        return
    _sink.emit(
        LogRecord(
            evt=evt,
            tier=tier,
            lvl=lvl,
            msg=msg,
            src=src,
            data=data,
            session_id=session_id,
        )
    )


def configure_logging(
    level: Optional[int] = None,
    root: Optional[str] = None,
    prune: bool = True,
) -> LogSink:
    """Start the diagnostic log for this process.  Idempotent."""
    global _sink, _configured
    with _lock:
        if _configured and _sink is not None:
            return _sink

        if level is None:
            level = _level_from_env()

        _sink = LogSink(root=root).start()
        install_bridge(_sink, level=level)
        _quiet_noisy_loggers()
        _write_meta(_sink)
        _install_crash_hooks(_sink)
        _configured = True

    if prune:
        try:
            removed = prune_runs(root=_sink.root)
            if removed:
                log("log.pruned", msg=f"Removed {removed} old run director{'y' if removed == 1 else 'ies'}",
                    src="reacher.diagnostics", removed=removed)
        except Exception:
            pass

    log(
        "app.boot",
        msg=f"REACHER starting (run {context.RUN_ID})",
        src="reacher.diagnostics",
        log_path=_sink.path,
        level=logging.getLevelName(level),
    )
    return _sink


def _quiet_noisy_loggers() -> None:
    """Raise the level floor on chatty third-party loggers.

    ``REACHER_LOG_VERBOSE_DEPS=1`` disables this, for the rare case where the
    bug really is inside httpx or zeroconf.
    """
    if os.environ.get("REACHER_LOG_VERBOSE_DEPS"):
        return
    for name, level in QUIET_LOGGERS.items():
        logging.getLogger(name).setLevel(level)


def _level_from_env() -> int:
    """Resolve the file log level from ``REACHER_LOG_LEVEL``.

    Defaults to DEBUG: the whole point of this system is that a bug reported
    after the fact is already captured, which a default of INFO would defeat.
    """
    raw = os.environ.get("REACHER_LOG_LEVEL", "DEBUG").strip().upper()
    return getattr(logging, raw, logging.DEBUG) if raw.isalpha() else logging.DEBUG


def _write_meta(sink: LogSink) -> None:
    try:
        with open(os.path.join(sink.run_dir, "meta.json"), "w", encoding="utf-8") as fh:
            json.dump(context.process_meta(), fh, indent=2, default=str)
    except Exception:
        pass


# -- crash capture ---------------------------------------------------------


def _install_crash_hooks(sink: LogSink) -> None:
    """Capture every way this process can die."""
    global _crash_fh

    # Native faults (a segfault inside pyserial or an avrdude child) cannot be
    # caught in Python, so faulthandler needs a real file descriptor of its own.
    try:
        _crash_fh = open(os.path.join(sink.run_dir, "crash.txt"), "a", encoding="utf-8")
        faulthandler.enable(file=_crash_fh, all_threads=True)
    except Exception:
        pass

    prev_excepthook = sys.excepthook

    def _excepthook(exc_type, exc, tb):
        try:
            import traceback

            log(
                "app.crash",
                lvl="fatal",
                msg=f"Unhandled {exc_type.__name__}: {exc}",
                src="reacher.diagnostics",
                exc="".join(traceback.format_exception(exc_type, exc, tb)).strip(),
            )
            _flush()
        except Exception:
            pass
        prev_excepthook(exc_type, exc, tb)

    sys.excepthook = _excepthook

    prev_threadhook = getattr(threading, "excepthook", None)

    def _threadhook(args):
        try:
            import traceback

            log(
                "app.thread_crash",
                lvl="error",
                msg=f"Thread {getattr(args.thread, 'name', '?')} died: {args.exc_value}",
                src="reacher.diagnostics",
                thread=getattr(args.thread, "name", None),
                exc="".join(
                    traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
                ).strip(),
            )
        except Exception:
            pass
        if prev_threadhook is not None:
            prev_threadhook(args)

    if prev_threadhook is not None:
        threading.excepthook = _threadhook

    atexit.register(_on_exit)


def install_signal_handlers() -> None:
    """Log SIGINT/SIGTERM, then delegate to whatever handler was already set.

    Must be called *after* uvicorn installs its own handlers — i.e. from the
    lifespan startup, not from ``main()`` — otherwise uvicorn overwrites these.
    Chaining rather than replacing keeps graceful shutdown intact.

    This makes the WebSocket watchdog's self-SIGINT visible in the log instead
    of the process appearing to die spontaneously.
    """
    for signame in ("SIGINT", "SIGTERM", "SIGHUP"):
        sig = getattr(signal, signame, None)
        if sig is None:
            continue
        try:
            previous = signal.getsignal(sig)
        except (ValueError, OSError):
            continue
        if previous in _prev_signal_handlers.values():
            continue
        _prev_signal_handlers[sig] = previous

        def _handler(signum, frame, _sig=sig, _prev=previous):
            try:
                log(
                    "app.signal",
                    lvl="warn",
                    msg=f"Received {signal.Signals(signum).name}",
                    src="reacher.diagnostics",
                    signal=signal.Signals(signum).name,
                )
                _flush()
            except Exception:
                pass
            if callable(_prev):
                _prev(signum, frame)
            elif _prev == signal.SIG_DFL:
                signal.signal(_sig, signal.SIG_DFL)
                os.kill(os.getpid(), signum)

        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            # Not on the main thread (e.g. under TestClient) — not fatal.
            _prev_signal_handlers.pop(sig, None)


def _on_exit() -> None:
    try:
        log(
            "app.exit",
            msg=f"REACHER exiting after {context.uptime():.1f}s",
            src="reacher.diagnostics",
            uptime_s=round(context.uptime(), 3),
            **(_sink.stats() if _sink else {}),
        )
    except Exception:
        pass
    _flush()


def _flush() -> None:
    if _sink is not None:
        try:
            _sink.stop(timeout=2.0)
        except Exception:
            pass


def uvicorn_log_config() -> dict:
    """A uvicorn ``log_config`` that routes its loggers to the root handler.

    Replaces the ``log_config=None`` used in frozen builds, which disabled
    uvicorn's logging entirely and was part of why shipped builds were silent.
    ``propagate: True`` with no handlers of its own lets our root SinkHandler
    pick up access and error logs.
    """
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "handlers": {},
        "loggers": {
            "uvicorn": {"handlers": [], "level": "INFO", "propagate": True},
            "uvicorn.error": {"handlers": [], "level": "INFO", "propagate": True},
            "uvicorn.access": {"handlers": [], "level": "INFO", "propagate": True},
        },
    }


def reset_for_tests() -> None:
    """Tear down global state so a test can configure a fresh sink."""
    global _sink, _configured, _crash_fh
    with _lock:
        if _sink is not None:
            try:
                _sink.stop(timeout=1.0)
            except Exception:
                pass
        root = logging.getLogger()
        from .bridge import SinkHandler

        for handler in list(root.handlers):
            if isinstance(handler, SinkHandler):
                root.removeHandler(handler)
        if _crash_fh is not None:
            try:
                faulthandler.disable()
                _crash_fh.close()
            except Exception:
                pass
            _crash_fh = None
        _sink = None
        _configured = False

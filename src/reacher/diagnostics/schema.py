"""The diagnostic record: one schema shared by every producer.

Frontend, backend and serial-wire records all land in the same NDJSON stream
with the same shape, so a single pass over one file reconstructs a whole run.

Field notes:

``seq``
    Process-monotonic counter.  Gives a **total order** that does not depend on
    any clock.  This matters because UI records are stamped with *browser* time
    and arrive batched and late; sorting by ``ts`` alone would interleave them
    wrongly against backend records.
``mono``
    Seconds since process start, monotonic — survives NTP steps mid-run.
``tier``
    Which layer produced the record, so a reader can filter to just the wire or
    just the UI without knowing every event name.
"""

import itertools
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from . import context

#: Coarse layer labels.  Kept short because they repeat on every line.
TIER_APP = "app"        # process lifecycle: boot, shutdown, crash, signals
TIER_API = "api"        # HTTP + WebSocket surface
TIER_KERNEL = "kernel"  # session manager and REACHER kernel internals
TIER_WIRE = "wire"      # raw serial traffic, both directions
TIER_UI = "ui"          # browser-originated records, received via ingest

_LEVEL_NAMES = {
    10: "debug",
    20: "info",
    30: "warn",
    40: "error",
    50: "fatal",
}

#: Monotonic counter shared by every record in this process.  ``itertools.count``
#: is atomic under CPython's GIL, so this needs no lock on the hot path.
_seq = itertools.count(1)


def next_seq() -> int:
    return next(_seq)


def level_name(levelno: int) -> str:
    """Map a stdlib logging level number onto our five names."""
    for threshold in (50, 40, 30, 20):
        if levelno >= threshold:
            return _LEVEL_NAMES[threshold]
    return "debug"


@dataclass(slots=True)
class LogRecord:
    """One line of the diagnostic log."""

    evt: str
    tier: str
    lvl: str = "info"
    msg: str = ""
    src: str = ""
    data: dict = field(default_factory=dict)
    session_id: Optional[str] = None
    corr_id: Optional[str] = None
    ts: Optional[float] = None
    mono: Optional[float] = None
    seq: Optional[int] = None

    def finalize(self) -> "LogRecord":
        """Stamp ordering/correlation fields that must come from this process.

        Called on the producer's thread rather than in the writer so that ``seq``
        reflects the order events actually happened, not the order the writer
        drained them.
        """
        if self.seq is None:
            self.seq = next_seq()
        if self.ts is None:
            self.ts = time.time()
        if self.mono is None:
            self.mono = round(context.uptime(), 6)
        if self.corr_id is None:
            self.corr_id = context.get_corr_id()
        if self.session_id is None:
            self.session_id = context.get_session_id()
        return self

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ts": _iso(self.ts),
            "mono": self.mono,
            "seq": self.seq,
            "run_id": context.RUN_ID,
            "tier": self.tier,
            "lvl": self.lvl,
            "evt": self.evt,
            "src": self.src,
        }
        # Optional fields are omitted rather than written as null: at millions of
        # lines the saved bytes are real, and `jq` treats absent and null alike.
        if self.session_id:
            out["session_id"] = self.session_id
        if self.corr_id:
            out["corr_id"] = self.corr_id
        if self.msg:
            out["msg"] = self.msg
        if self.data:
            out["data"] = self.data
        return out

    def to_json(self) -> str:
        """Serialize to a single NDJSON line.

        Falls back to ``default=str`` and, failing that, to a minimal
        self-describing error record — a log line must never raise.
        """
        try:
            return json.dumps(self.to_dict(), separators=(",", ":"), default=str)
        except Exception as exc:
            return json.dumps(
                {
                    "ts": _iso(self.ts),
                    "seq": self.seq,
                    "run_id": context.RUN_ID,
                    "tier": self.tier,
                    "lvl": "error",
                    "evt": "log.serialize_failed",
                    "src": self.src,
                    "msg": f"{type(exc).__name__}: {exc}",
                },
                separators=(",", ":"),
            )


def _iso(ts: Optional[float]) -> str:
    """Render a POSIX timestamp as ISO-8601 UTC with millisecond precision."""
    if ts is None:
        ts = time.time()
    struct = time.gmtime(ts)
    ms = int((ts - int(ts)) * 1000)
    return f"{time.strftime('%Y-%m-%dT%H:%M:%S', struct)}.{ms:03d}Z"

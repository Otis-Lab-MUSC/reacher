"""Diagnostic log ingest and retrieval.

The browser has no filesystem, so UI records reach disk by being POSTed here and
written through the same sink the backend uses.  That is what makes a single
file a complete record of a run rather than a backend-only view.

These routes live under ``/api``, so the existing proxy router relays them
unchanged: the primary machine pulls a paired host's logs via
``/api/proxy/{device_id}/api/logs/export`` with no new transport.
"""

import io
import os
import time
import zipfile
from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ... import diagnostics
from ...diagnostics.redact import redact, redact_ui_field
from ...diagnostics.schema import TIER_UI, LogRecord
from ...diagnostics.sink import default_log_root

router = APIRouter()

#: Caps on a single ingest batch.  This endpoint is reachable by anything
#: holding the API key, so it is bounded rather than trusting the caller.
MAX_BATCH = 500
MAX_MESSAGE = 2000
MAX_EVT = 120

#: Simple per-process token bucket: sustained records/second and burst size.
_RATE_PER_SEC = 500.0
_BURST = 2000.0
_tokens = _BURST
_last_refill = time.monotonic()


class UIRecord(BaseModel):
    """One browser-originated record.

    Field names mirror the on-disk schema so a UI record and a backend record
    read identically once written.
    """

    evt: str
    lvl: str = "info"
    msg: str = ""
    src: str = ""
    ts: Optional[float] = None
    session_id: Optional[str] = None
    corr_id: Optional[str] = None
    data: dict[str, Any] = Field(default_factory=dict)


class IngestBatch(BaseModel):
    records: List[UIRecord]


def _take_tokens(count: int) -> bool:
    """Token-bucket admission.  Returns False when the caller is over budget."""
    global _tokens, _last_refill
    now = time.monotonic()
    _tokens = min(_BURST, _tokens + (now - _last_refill) * _RATE_PER_SEC)
    _last_refill = now
    if _tokens < count:
        return False
    _tokens -= count
    return True


_ALLOWED_LEVELS = {"debug", "info", "warn", "error", "fatal"}


@router.post("/ingest")
async def ingest(batch: IngestBatch):
    """Accept a batch of browser records and write them to the run log."""
    sink = diagnostics.get_sink()
    if sink is None:
        raise HTTPException(status_code=503, detail="Logging is not configured")

    if len(batch.records) > MAX_BATCH:
        raise HTTPException(status_code=413, detail=f"Batch exceeds {MAX_BATCH} records")

    if not _take_tokens(len(batch.records)):
        raise HTTPException(status_code=429, detail="Log ingest rate limit exceeded")

    now = time.time()
    accepted = 0
    for item in batch.records:
        lvl = item.lvl if item.lvl in _ALLOWED_LEVELS else "info"

        # Browser clocks are not trustworthy for ordering, and a wildly wrong
        # one would scramble the timeline.  Keep the client's stamp as data and
        # let the record's own ts come from this process.
        data = redact(redact_ui_field(item.data)) if item.data else {}
        if item.ts:
            skew = now - (item.ts / 1000.0 if item.ts > 1e11 else item.ts)
            if abs(skew) > 2.0:
                data["client_clock_skew_s"] = round(skew, 3)

        sink.emit(
            LogRecord(
                evt=item.evt[:MAX_EVT] or "ui.unknown",
                tier=TIER_UI,
                lvl=lvl,
                msg=item.msg[:MAX_MESSAGE],
                src=item.src[:120] or "labrynth.web",
                data=data,
                session_id=item.session_id,
                corr_id=item.corr_id,
            )
        )
        accepted += 1

    return {"accepted": accepted}


def _run_root() -> str:
    return default_log_root()


def _safe_run_dir(run: str) -> str:
    """Resolve *run* to a directory inside the log root, or 404.

    ``run`` comes from the client, so it is resolved and then checked to be a
    child of the log root — otherwise ``../../etc`` would escape it.
    """
    root = os.path.realpath(_run_root())
    candidate = os.path.realpath(os.path.join(root, run))
    if not (candidate == root or candidate.startswith(root + os.sep)):
        raise HTTPException(status_code=400, detail="Invalid run identifier")
    if not os.path.isdir(candidate):
        raise HTTPException(status_code=404, detail="Run not found")
    return candidate


@router.get("/runs")
async def list_runs():
    """List available run directories, newest first."""
    root = _run_root()
    sink = diagnostics.get_sink()
    current = os.path.basename(sink.run_dir) if sink else None
    if not os.path.isdir(root):
        return {"root": root, "current": current, "runs": []}

    runs = []
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if name == "latest" or not os.path.isdir(path):
            continue
        size = 0
        for entry in os.scandir(path):
            if entry.is_file():
                try:
                    size += entry.stat().st_size
                except OSError:
                    pass
        runs.append(
            {
                "run": name,
                "bytes": size,
                "modified": os.path.getmtime(path),
                "current": name == current,
            }
        )
    runs.sort(key=lambda r: r["modified"], reverse=True)
    return {"root": root, "current": current, "runs": runs}


@router.get("/export")
async def export_run(run: Optional[str] = None):
    """Download a run directory as a ZIP, for attaching to a bug report."""
    sink = diagnostics.get_sink()
    if run is None:
        if sink is None:
            raise HTTPException(status_code=404, detail="No active run")
        run = os.path.basename(sink.run_dir)
    run_dir = _safe_run_dir(run)

    # Flush first, or the tail of the very run being exported is still sitting
    # in the writer's buffer and missing from the download.
    if sink is not None and os.path.realpath(sink.run_dir) == run_dir:
        sink.flush_now()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, _dirnames, filenames in os.walk(run_dir):
            for filename in filenames:
                full = os.path.join(dirpath, filename)
                arcname = os.path.join(run, os.path.relpath(full, run_dir))
                try:
                    zf.write(full, arcname)
                except OSError:
                    continue
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="reacher-logs-{run}.zip"'},
    )

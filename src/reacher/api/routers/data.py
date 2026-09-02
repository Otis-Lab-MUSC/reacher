"""Data retrieval endpoints (behavior, frames, slm)."""

from fastapi import APIRouter, HTTPException, Request
from typing import Optional

router = APIRouter()


@router.get("/{session_id}/behavior")
async def get_behavior(
    session_id: str,
    request: Request,
    since: Optional[int] = None,
    limit: Optional[int] = None,
):
    sm = request.app.state.session_manager
    try:
        info = sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    if limit is not None and not (1 <= limit <= 100000):
        raise HTTPException(status_code=400, detail="limit must be between 1 and 100000")

    data = info.instance.get_behavior_data()
    if since is not None and since >= 0:
        data = data[since:]
    if limit is not None:
        data = data[:limit]
    return {"data": data, "total": len(info.instance.get_behavior_data())}


@router.get("/{session_id}/frames")
async def get_frames(
    session_id: str,
    request: Request,
    limit: Optional[int] = None,
):
    sm = request.app.state.session_manager
    try:
        info = sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    if limit is not None and not (1 <= limit <= 100000):
        raise HTTPException(status_code=400, detail="limit must be between 1 and 100000")

    frames = info.instance.get_frame_data()
    if limit is not None:
        frames = frames[:limit]
    return {
        "frames": frames,
        "count": info.instance.get_frame_timestamps_count(),
    }


@router.get("/{session_id}/slm")
async def get_slm(
    session_id: str,
    request: Request,
    limit: Optional[int] = None,
):
    sm = request.app.state.session_manager
    try:
        info = sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    if limit is not None and not (1 <= limit <= 100000):
        raise HTTPException(status_code=400, detail="limit must be between 1 and 100000")

    slm = info.instance.get_slm_data()
    if limit is not None:
        slm = slm[:limit]
    return {
        "slm": slm,
        "count": len(slm),
    }


@router.get("/{session_id}/recovery")
async def get_recovery(session_id: str, request: Request):
    """Unified snapshot of the behavior, frame, and SLM streams.

    Issue labrynth#100: a WS-reconnecting client used to recover missed
    behavior events via `/behavior` alone and compare its `total` against the
    length of a local array that also held SLM ticks (behavior_data never
    stores those — see `update_slm_events`). That cross-stream comparison
    could both silently miss real behavior events and wipe every locally-known
    SLM tick on a full replace.

    `behavior_data` stays untouched (it also backs the CSV export and segment
    paths, so folding SLM into it would ripple into exported files for what is
    only a reconnect bug). Instead this bundles full, mutually-authoritative
    snapshots of all three streams in one response — one consistent point in
    time rather than three separate requests racing session state — so a
    client can reconcile each stream against its own prior count instead of
    against another stream's.
    """
    sm = request.app.state.session_manager
    try:
        info = sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    instance = info.instance
    behavior = instance.get_behavior_data()
    frames = instance.get_frame_data()
    slm = instance.get_slm_data()
    return {
        "behavior": {"data": behavior, "total": len(behavior)},
        "frames": {"frames": frames, "count": len(frames)},
        "slm": {"slm": slm, "count": len(slm)},
    }

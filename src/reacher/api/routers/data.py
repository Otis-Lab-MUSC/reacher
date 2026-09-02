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
    """Bundled snapshot of the behavior, frame, and SLM streams — NOT symmetric.

    Issue labrynth#100: a WS-reconnecting client used to recover missed
    behavior events via `/behavior` alone and compare its `total` against the
    length of a local array that also held SLM ticks (behavior_data never
    stores those — see `update_slm_events`). That cross-stream comparison
    could both silently miss real behavior events and wipe every locally-known
    SLM tick on a full replace.

    `behavior_data` stays untouched (it also backs the CSV export and segment
    paths, so folding SLM into it would ripple into exported files for what is
    only a reconnect bug). Instead this bundles snapshots of all three streams
    in one response so a client can reconcile each stream against its own
    prior count instead of against another stream's.

    **The `behavior` stream is current-segment-only; `frames` and `slm` are
    whole-session.** `split_segment()` exports and clears `behavior_data` on
    every split, by design, but deliberately leaves `frame_data`/`slm_data`
    alone ("frame indices remain continuous across splits" — see its
    docstring). So after a split, `behavior.total` only covers events since
    the last split while `frames.count`/`slm.count` cover the whole session.
    `segment_number` (0 if no split has occurred) is included so a caller can
    detect this and must not treat `behavior.data` as a full-session replacement
    once it is nonzero — there is no cross-segment behavior accessor
    (`get_total_infusion_count`/`get_total_press_count`/`get_total_trial_count`
    exist for the derived counters; the event list itself has no counterpart,
    and the previously-split segments are already safely on disk as CSVs via
    `get_segment_exports()`, not held in memory to hand back).

    Not a single atomic snapshot: the three `get_*_data()` calls each
    lock/copy/unlock sequentially, so an event landing between two of them can
    appear in one stream and not another. Low severity given each stream is
    compared against its own prior count rather than against each other, but
    real under concurrent write.
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
        "segment_number": instance.get_segment_number(),
    }

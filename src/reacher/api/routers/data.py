"""Data retrieval endpoints (behavior, frames)."""

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

"""Data retrieval endpoints (behavior, frames)."""

from fastapi import APIRouter, HTTPException, Request
from typing import Optional

router = APIRouter()


@router.get("/{session_id}/behavior")
async def get_behavior(session_id: str, request: Request, since: Optional[int] = None):
    sm = request.app.state.session_manager
    try:
        info = sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    data = info.instance.get_behavior_data()
    if since is not None and since >= 0:
        data = data[since:]
    return {"data": data, "total": len(info.instance.get_behavior_data())}


@router.get("/{session_id}/frames")
async def get_frames(session_id: str, request: Request):
    sm = request.app.state.session_manager
    try:
        info = sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    return {
        "frames": info.instance.get_frame_data(),
        "count": info.instance.get_frame_timestamps_count(),
    }

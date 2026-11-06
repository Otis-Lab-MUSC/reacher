"""Data retrieval endpoints (behavior, frames, slm)."""

from typing import Optional, TypedDict

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


class _BehaviorPage(TypedDict):
    data: list[dict[str, str | int]]
    total: int


class _FramePage(TypedDict):
    frames: list[int]
    count: int


class _SlmPage(TypedDict):
    slm: list[int]
    count: int


@router.get("/{session_id}/behavior", response_model=None)
async def get_behavior(
    session_id: str,
    request: Request,
    since: Optional[int] = None,
    limit: Optional[int] = None,
) -> _BehaviorPage:
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


@router.get("/{session_id}/frames", response_model=None)
async def get_frames(
    session_id: str,
    request: Request,
    limit: Optional[int] = None,
) -> _FramePage:
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


@router.get("/{session_id}/slm", response_model=None)
async def get_slm(
    session_id: str,
    request: Request,
    limit: Optional[int] = None,
) -> _SlmPage:
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

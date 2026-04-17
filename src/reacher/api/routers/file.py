"""File configuration endpoints (filename, destination, folder creation, ZIP export)."""

import bisect
import csv
import io
import json
import logging
import os
import re
import time
import zipfile
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional

router = APIRouter()
logger = logging.getLogger(__name__)


_ARCHIVE_SUFFIXES = (".tar.gz", ".tgz", ".zip", ".tar", ".gz")


def _strip_archive_suffix(name: str) -> str:
    """Strip one trailing archive extension so `{name}.zip` never becomes `*.zip.zip`."""
    if not name:
        return name
    lower = name.lower()
    for suf in _ARCHIVE_SUFFIXES:  # longest-first
        if lower.endswith(suf):
            return name[: -len(suf)]
    return name


def _sanitize_for_path(name: str) -> str:
    """Replace characters unsafe for directory/file names with underscores."""
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    sanitized = sanitized.replace(' ', '_')
    sanitized = re.sub(r'_+', '_', sanitized)
    return sanitized.strip(' _.') or "session"


def _find_frame_index(frame_timestamps: list[int], event_ts: int) -> int | None:
    """Return the index of the last frame at or before *event_ts*, or None."""
    if not frame_timestamps:
        return None
    idx = bisect.bisect_right(frame_timestamps, event_ts) - 1
    if idx < 0:
        return None
    return idx


def _build_behavior_csv(behavior: list, frame_timestamps: list[int]) -> str:
    """Serialise a behavior event list to CSV matching the on-disk segment format."""
    csv_buf = io.StringIO()
    fieldnames = ["device", "event", "start_timestamp", "end_timestamp", "start_frame_index", "end_frame_index"]
    writer = csv.DictWriter(csv_buf, fieldnames=fieldnames)
    writer.writeheader()
    for row in behavior:
        start_ts = row.get("start_timestamp")
        end_ts = row.get("end_timestamp")
        start_fi = _find_frame_index(frame_timestamps, int(start_ts)) if start_ts not in (None, "") else None
        end_fi = _find_frame_index(frame_timestamps, int(end_ts)) if end_ts not in (None, "") else None
        out = {k: row.get(k, "") for k in ("device", "event", "start_timestamp", "end_timestamp")}
        out["start_frame_index"] = start_fi if start_fi is not None else ""
        out["end_frame_index"] = end_fi if end_fi is not None else ""
        writer.writerow(out)
    return csv_buf.getvalue()


class FileConfigRequest(BaseModel):
    filename: Optional[str] = None
    destination: Optional[str] = None


@router.post("/{session_id}/config")
async def set_file_config(session_id: str, body: FileConfigRequest, request: Request):
    sm = request.app.state.session_manager
    try:
        info = sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    instance = info.instance

    if body.filename is not None:
        if os.sep in body.filename or "/" in body.filename:
            raise HTTPException(status_code=400, detail="Filename must not contain path separators")
        instance.set_filename(_strip_archive_suffix(body.filename))

    if body.destination is not None:
        resolved = os.path.realpath(os.path.expanduser(body.destination))
        home = os.path.realpath(os.path.expanduser("~"))
        if not (resolved == home or resolved.startswith(home + os.sep)):
            raise HTTPException(status_code=400, detail="Destination must be within home directory")
        instance.set_data_destination(resolved)

    return {
        "filename": instance.get_filename(),
        "destination": instance.get_data_destination(),
    }


@router.post("/{session_id}/create_folder")
async def create_folder(session_id: str, request: Request):
    sm = request.app.state.session_manager
    try:
        info = sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        folder = info.instance.make_destination_folder()
    except Exception:
        logger.error("Folder creation failed for session %s", session_id, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create folder")

    return {"folder_path": folder}


class ZipExportRequest(BaseModel):
    session_name: Optional[str] = None
    notes: Optional[str] = None
    infusion_count: int = 0
    press_count: int = 0
    trial_count: int = 0
    program_start_time: Optional[float] = None


@router.post("/{session_id}/export/zip")
async def export_zip(session_id: str, body: ZipExportRequest, request: Request):
    sm = request.app.state.session_manager
    try:
        info = sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    instance = info.instance
    filename = _strip_archive_suffix(instance.get_filename() or "")
    destination = instance.get_data_destination()

    if not filename:
        session_name = body.session_name or ""
        default_pattern = f"{(info.paradigm or 'Session').upper()} {info.port}"

        if session_name and session_name != default_pattern:
            # Manually named session — use as-is (sanitized)
            filename = _strip_archive_suffix(_sanitize_for_path(session_name))
        else:
            # Auto-default or empty — paradigm + port + datetime
            base = _sanitize_for_path(default_pattern)
            timestamp = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime())
            filename = f"{base}_{timestamp}"

    instance.set_filename(filename)
    if not destination:
        destination = os.path.expanduser("~/Downloads")
        instance.set_data_destination(destination)

    folder_path = instance.make_destination_folder()

    # Gather data
    behavior = instance.get_behavior_data()
    firmware_info = instance.get_firmware_information()
    hardware_settings = instance.get_hardware_settings()
    frame_data = instance.get_frame_data()
    frame_timestamps = sorted(int(ts) for ts in frame_data if ts)
    frame_count = len(frame_data)
    segment_exports = instance.get_segment_exports()
    prior_segment_counts = instance.get_segment_event_counts()

    # Build ZIP in memory
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if segment_exports:
            # Segmented session — include each prior segment CSV verbatim from disk
            for seg_path in segment_exports:
                if os.path.isfile(seg_path):
                    with open(seg_path, "rb") as f:
                        zf.writestr(os.path.basename(seg_path), f.read())
                else:
                    logger.warning("Segment CSV missing from log dir: %s", seg_path)

            # Final (current) segment — serialise from the in-memory buffer
            final_segment_number = len(segment_exports) + 1
            final_csv = _build_behavior_csv(behavior, frame_timestamps)
            zf.writestr(f"behavior_events_{final_segment_number:03d}.csv", final_csv)

            per_segment_event_counts = list(prior_segment_counts) + [len(behavior)]
            if len(per_segment_event_counts) != final_segment_number:
                per_segment_event_counts = per_segment_event_counts[:final_segment_number]
                while len(per_segment_event_counts) < final_segment_number:
                    per_segment_event_counts.append(0)
            segment_count = final_segment_number
            total_event_count = sum(per_segment_event_counts)
        else:
            # Non-segmented — single behavior_events.csv
            zf.writestr("behavior_events.csv", _build_behavior_csv(behavior, frame_timestamps))
            per_segment_event_counts = [len(behavior)]
            segment_count = 1
            total_event_count = len(behavior)

        # frame_timestamps.csv — only when microscope data was captured
        if frame_timestamps:
            ft_buf = io.StringIO()
            ft_writer = csv.DictWriter(ft_buf, fieldnames=["frame_index", "timestamp_ms"])
            ft_writer.writeheader()
            for i, ts in enumerate(frame_timestamps):
                ft_writer.writerow({"frame_index": i, "timestamp_ms": ts})
            zf.writestr("frame_timestamps.csv", ft_buf.getvalue())

        # arduino_config.json
        zf.writestr(
            "arduino_config.json",
            json.dumps(
                {"firmware_info": firmware_info, "hardware_settings": hardware_settings},
                indent=2,
            ),
        )

        # event_log.jsonl — authoritative cross-segment record of lifecycle, events, frames
        try:
            instance.flush_event_log()
            event_log_path = instance.get_event_log_path()
            if os.path.isfile(event_log_path):
                with open(event_log_path, "rb") as f:
                    zf.writestr("event_log.jsonl", f.read())
            else:
                logger.info("event_log.jsonl not present for session %s — skipping", session_id)
        except Exception:
            logger.warning("Failed to include event_log.jsonl for session %s", session_id, exc_info=True)

        # metadata.json
        now = time.time()
        export_date = time.strftime("%Y-%m-%d", time.localtime(now))
        export_time = time.strftime("%H:%M:%S", time.localtime(now))
        program_start_str = None
        if body.program_start_time is not None:
            program_start_str = time.strftime(
                "%H:%M:%S",
                time.localtime(body.program_start_time / 1000),
            )
        zf.writestr(
            "metadata.json",
            json.dumps(
                {
                    "session_id": session_id,
                    "session_name": body.session_name or None,
                    "port": info.port,
                    "paradigm": info.paradigm,
                    "firmware_sketch": firmware_info.get('sketch', 'unknown.ino'),
                    "firmware_version": firmware_info.get("version", "unknown"),
                    "export_date": export_date,
                    "export_time": export_time,
                    "program_start_time": program_start_str,
                    "behavior_event_count": total_event_count,
                    "segment_count": segment_count,
                    "per_segment_event_counts": per_segment_event_counts,
                    "frame_count": frame_count,
                    "infusion_count": body.infusion_count,
                    "press_count": body.press_count,
                    "trial_count": body.trial_count,
                },
                indent=2,
            ),
        )

        # notes.txt (only if non-empty)
        if body.notes and body.notes.strip():
            zf.writestr("notes.txt", body.notes)

    # Write to disk
    zip_path = os.path.join(folder_path, f"{filename}.zip")
    with open(zip_path, "wb") as f:
        f.write(buf.getvalue())

    return {"file_path": zip_path, "folder_path": folder_path}


@router.get("/{session_id}/export/download")
async def download_export(session_id: str, path: str = Query(...), request: Request = None):
    """Return a previously exported ZIP as a binary download to the browser."""
    sm = request.app.state.session_manager
    try:
        sm.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    resolved = os.path.realpath(path)
    home = os.path.realpath(os.path.expanduser("~"))
    if not resolved.startswith(home + os.sep):
        raise HTTPException(status_code=403, detail="Access denied")
    if not os.path.isfile(resolved):
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(resolved, media_type="application/zip", filename=os.path.basename(resolved))

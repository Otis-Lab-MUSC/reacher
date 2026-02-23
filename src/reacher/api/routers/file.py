"""File configuration endpoints (filename, destination, folder creation, ZIP export)."""

import csv
import io
import json
import logging
import os
import time
import zipfile
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

router = APIRouter()
logger = logging.getLogger(__name__)


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
        instance.set_filename(body.filename)

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
    filename = instance.get_filename()
    destination = instance.get_data_destination()

    if not filename or not destination:
        raise HTTPException(
            status_code=400,
            detail="Filename and destination must be configured before exporting",
        )

    folder_path = instance.make_destination_folder()

    # Gather data
    behavior = instance.get_behavior_data()
    firmware_info = instance.get_firmware_information()
    hardware_settings = instance.get_hardware_settings()
    frame_count = instance.get_frame_timestamps_count()

    # Build ZIP in memory
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # behavior_events.csv
        csv_buf = io.StringIO()
        fieldnames = ["device", "event", "start_timestamp", "end_timestamp"]
        writer = csv.DictWriter(csv_buf, fieldnames=fieldnames)
        writer.writeheader()
        for row in behavior:
            writer.writerow({k: row.get(k, "") for k in fieldnames})
        zf.writestr("behavior_events.csv", csv_buf.getvalue())

        # arduino_config.json
        zf.writestr(
            "arduino_config.json",
            json.dumps(
                {"firmware_info": firmware_info, "hardware_settings": hardware_settings},
                indent=2,
            ),
        )

        # metadata.json
        now = time.time()
        export_date = time.strftime("%Y-%m-%d", time.localtime(now))
        export_time = time.strftime("%H:%M:%S", time.localtime(now))
        zf.writestr(
            "metadata.json",
            json.dumps(
                {
                    "session_id": session_id,
                    "session_name": body.session_name or None,
                    "port": info.port,
                    "paradigm": info.paradigm,
                    "export_date": export_date,
                    "export_time": export_time,
                    "program_start_time": body.program_start_time,
                    "behavior_event_count": len(behavior),
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

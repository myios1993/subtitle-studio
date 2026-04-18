"""
Audio device enumeration and video file streaming endpoints.
"""

import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.project import Project
from backend.services.audio_capture import (
    list_microphone_devices,
    list_loopback_devices,
    get_default_loopback_device,
)

router = APIRouter(prefix="/api/audio", tags=["audio"])


@router.get("/devices")
async def get_audio_devices():
    """
    List all available audio input devices.
    Returns microphone and loopback (WASAPI) devices separately.
    """
    mics = list_microphone_devices()
    loopbacks = list_loopback_devices()
    default_loopback = get_default_loopback_device()

    return {
        "microphones": mics,
        "loopbacks": loopbacks,
        "default_loopback": default_loopback,
    }


# ---------------------------------------------------------------------------
# Video file streaming (HTTP Range support for <video> seek)
# ---------------------------------------------------------------------------

CHUNK_SIZE = 1024 * 1024  # 1MB per chunk


@router.get("/video/{project_id}")
async def stream_video(
    project_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Stream a project's playback video with HTTP Range support.
    This enables the browser <video> element to seek freely.
    Falls back to source_video_path if no playback version exists.
    """
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    video_path = project.playback_video_path or project.source_video_path
    if not video_path or not Path(video_path).exists():
        raise HTTPException(status_code=404, detail="No video file available")

    file_path = Path(video_path)
    file_size = file_path.stat().st_size

    # Determine content type from extension
    ext = file_path.suffix.lower()
    content_types = {
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".mkv": "video/x-matroska",
        ".avi": "video/x-msvideo",
    }
    content_type = content_types.get(ext, "video/mp4")

    # Parse Range header
    range_header = request.headers.get("range")
    if range_header:
        # Parse "bytes=START-END" or "bytes=START-"
        range_spec = range_header.replace("bytes=", "")
        parts = range_spec.split("-")
        start = int(parts[0]) if parts[0] else 0
        end = int(parts[1]) if parts[1] else file_size - 1
        end = min(end, file_size - 1)
        content_length = end - start + 1

        def _range_generator():
            with open(file_path, "rb") as f:
                f.seek(start)
                remaining = content_length
                while remaining > 0:
                    chunk = f.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        return StreamingResponse(
            _range_generator(),
            status_code=206,
            media_type=content_type,
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(content_length),
            },
        )
    else:
        # Full file response
        def _full_generator():
            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    yield chunk

        return StreamingResponse(
            _full_generator(),
            media_type=content_type,
            headers={
                "Accept-Ranges": "bytes",
                "Content-Length": str(file_size),
            },
        )

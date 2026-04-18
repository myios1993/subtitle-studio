"""
Setup status API.

Provides a single endpoint that checks whether all required components
(ffmpeg, Whisper model, pyannote model) are present, so the frontend
can show a guided wizard on first run.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from fastapi import APIRouter

from backend.config import settings

router = APIRouter(prefix="/api/setup", tags=["setup"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_ffmpeg() -> dict:
    path = shutil.which(settings.ffmpeg_path) or settings.ffmpeg_path
    # CREATE_NO_WINDOW prevents a console window from flashing on Windows
    _no_window = 0x08000000 if subprocess.sys.platform == "win32" else 0
    try:
        result = subprocess.run(
            [path, "-version"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=_no_window,
        )
        if result.returncode == 0:
            first_line = result.stdout.splitlines()[0] if result.stdout else ""
            version = first_line.replace("ffmpeg version ", "").split(" ")[0]
            return {"available": True, "path": path, "version": version}
    except Exception:
        pass
    return {"available": False, "path": path, "version": None}


def _check_whisper() -> dict:
    size = settings.whisper_model_size
    folder = f"models--Systran--faster-whisper-{size}"
    snapshots = Path(settings.whisper_model_dir) / folder / "snapshots"
    downloaded = False
    if snapshots.exists():
        for snap in snapshots.iterdir():
            if snap.is_dir() and any(snap.iterdir()):
                downloaded = True
                break
    return {
        "downloaded": downloaded,
        "size": size,
        "model_dir": settings.whisper_model_dir,
    }


def _check_pyannote() -> dict:
    from backend.services.diarization import get_diarization_service, get_pyannote_download_status

    info = get_pyannote_download_status()
    svc = get_diarization_service()
    return {
        **info,
        "loaded": svc._loaded,
        "download_running": svc.download_running,
        "download_progress": round(svc.download_progress, 3),
        "download_message": svc.download_message,
        "download_error": svc.download_error,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/status")
async def setup_status():
    """
    Return a unified readiness status used by the frontend setup wizard.

    `ready` is True when ffmpeg and the Whisper model are available.
    pyannote is optional (diarization will be skipped if absent).
    """
    ffmpeg = _check_ffmpeg()
    whisper = _check_whisper()
    pyannote = _check_pyannote()

    ready = ffmpeg["available"] and whisper["downloaded"]

    return {
        "ffmpeg": ffmpeg,
        "whisper": whisper,
        "pyannote": pyannote,
        "ready": ready,
    }

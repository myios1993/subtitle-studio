"""
Model management API endpoints.

Provides:
  GET  /api/models/status            — check which models are downloaded
  POST /api/models/whisper/download  — trigger Whisper model download (background)
  GET  /api/models/whisper/progress  — poll Whisper download progress
  POST /api/models/pyannote/download — trigger pyannote download (background)
  GET  /api/models/pyannote/progress — poll pyannote download progress
  POST /api/models/pyannote/load     — load pyannote into memory
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import get_db
from backend.models.settings import AppSettings
from backend.services.diarization import get_diarization_service, get_pyannote_download_status
from sqlalchemy import select

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/models", tags=["models"])

# ── Whisper download state ─────────────────────────────────────────────────

_whisper_download_running = False
_whisper_download_progress = 0.0
_whisper_download_message = ""
_whisper_download_error: str | None = None


# ---------------------------------------------------------------------------
# Whisper model detection helpers
# ---------------------------------------------------------------------------

_WHISPER_SIZES = ["tiny", "base", "small", "medium", "large-v3", "distil-large-v3"]


def _whisper_cache_dir(size: str) -> Path:
    """
    faster-whisper uses the same HF cache format:
    models/whisper/models--Systran--faster-whisper-{size}/
    """
    folder = f"models--Systran--faster-whisper-{size}"
    return Path(settings.whisper_model_dir) / folder


def _whisper_is_downloaded(size: str) -> bool:
    p = _whisper_cache_dir(size)
    snapshots = p / "snapshots"
    if not snapshots.exists():
        return False
    for snap in snapshots.iterdir():
        if snap.is_dir() and any(snap.iterdir()):
            return True
    return False


def _get_whisper_status() -> dict:
    return {
        size: _whisper_is_downloaded(size)
        for size in _WHISPER_SIZES
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/status")
async def models_status():
    """
    Return download status for all models:
      - Whisper (each size)
      - pyannote speaker-diarization-3.1 (all sub-models)
    """
    diarization_svc = get_diarization_service()

    pyannote_info = get_pyannote_download_status()
    whisper_info = _get_whisper_status()

    return {
        "whisper": {
            "sizes": whisper_info,
            "current_model_size": settings.whisper_model_size,
            "model_dir": settings.whisper_model_dir,
        },
        "pyannote": {
            **pyannote_info,
            "loaded": diarization_svc._loaded,
            "download_running": diarization_svc.download_running,
            "download_progress": round(diarization_svc.download_progress, 3),
            "download_message": diarization_svc.download_message,
            "download_error": diarization_svc.download_error,
        },
    }


class PyannoteDownloadRequest(BaseModel):
    hf_token: str | None = None  # optional: fall back to DB stored token


@router.post("/pyannote/download")
async def download_pyannote(
    body: PyannoteDownloadRequest = PyannoteDownloadRequest(),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger an async download of all pyannote sub-models.
    The download runs in the background; poll /api/models/pyannote/progress.

    If hf_token is not provided in the body, falls back to the stored
    'hf_token' setting in the database.
    """
    diarization_svc = get_diarization_service()

    if diarization_svc.download_running:
        return {
            "message": "下载已在进行中",
            "progress": diarization_svc.download_progress,
        }

    if diarization_svc.is_downloaded:
        return {"message": "pyannote 模型已存在于本地，无需下载"}

    # Resolve token: body → DB → error
    token = body.hf_token
    if not token:
        stmt = select(AppSettings).where(AppSettings.key == "hf_token")
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()
        if row and row.value and row.value != "":
            token = row.value

    if not token:
        raise HTTPException(
            status_code=400,
            detail=(
                "需要 HuggingFace Token 才能下载 pyannote 模型。"
                "请在设置页填写 Token，或在请求 body 中提供 hf_token 字段。"
            ),
        )

    # Fire-and-forget background download
    background_tasks.add_task(_run_download, token)

    return {
        "message": "下载已启动，请轮询 /api/models/pyannote/progress 获取进度",
        "progress": 0.0,
    }


@router.get("/pyannote/progress")
async def pyannote_progress():
    """Poll the current pyannote download progress."""
    svc = get_diarization_service()
    return {
        "running": svc.download_running,
        "progress": round(svc.download_progress, 3),
        "message": svc.download_message,
        "error": svc.download_error,
        "downloaded": svc.is_downloaded,
        "loaded": svc._loaded,
    }


@router.post("/pyannote/load")
async def load_pyannote():
    """
    Load the pyannote pipeline into memory from local cache.
    No-op if already loaded. Returns error if models not downloaded yet.
    """
    svc = get_diarization_service()

    if svc._loaded:
        return {"message": "pyannote 已加载", "loaded": True}

    if not svc.is_downloaded:
        raise HTTPException(
            status_code=400,
            detail="pyannote 模型尚未下载。请先调用 /api/models/pyannote/download。",
        )

    ok = await svc.load_model()
    if not ok:
        raise HTTPException(status_code=500, detail="加载 pyannote 模型失败，请查看后端日志。")

    return {"message": "pyannote 加载成功", "loaded": True}


# ---------------------------------------------------------------------------
# Whisper download endpoints
# ---------------------------------------------------------------------------


@router.post("/whisper/download")
async def download_whisper(background_tasks: BackgroundTasks = BackgroundTasks()):
    """
    Trigger a background download of the configured Whisper model.
    Uses faster-whisper's built-in HF download via WhisperModel init.
    Poll /api/models/whisper/progress for status.
    """
    global _whisper_download_running, _whisper_download_error

    if _whisper_download_running:
        return {"message": "Whisper 下载已在进行中", "progress": _whisper_download_progress}

    # Check already downloaded
    if _whisper_is_downloaded(settings.whisper_model_size):
        return {"message": "Whisper 模型已存在", "downloaded": True}

    background_tasks.add_task(_run_whisper_download)
    return {"message": "Whisper 下载已启动", "progress": 0.0}


@router.get("/whisper/progress")
async def whisper_progress():
    """Poll the current Whisper download/load progress."""
    return {
        "running": _whisper_download_running,
        "progress": _whisper_download_progress,
        "message": _whisper_download_message,
        "error": _whisper_download_error,
        "downloaded": _whisper_is_downloaded(settings.whisper_model_size),
    }


# ---------------------------------------------------------------------------
# Background task helpers
# ---------------------------------------------------------------------------

async def _run_whisper_download() -> None:
    """
    Download the Whisper model via huggingface_hub.snapshot_download().
    This avoids loading the model into memory just to trigger the download.
    """
    global _whisper_download_running, _whisper_download_progress
    global _whisper_download_message, _whisper_download_error

    _whisper_download_running = True
    _whisper_download_progress = 0.0
    _whisper_download_message = "正在下载 Whisper 模型…"
    _whisper_download_error = None

    size = settings.whisper_model_size
    repo_id = f"Systran/faster-whisper-{size}"

    try:
        from huggingface_hub import snapshot_download
        import threading

        loop = asyncio.get_event_loop()

        def _do_download():
            global _whisper_download_message
            _whisper_download_message = f"下载 {repo_id} 中…"
            snapshot_download(
                repo_id=repo_id,
                cache_dir=settings.whisper_model_dir,
                local_files_only=False,
            )

        await loop.run_in_executor(None, _do_download)
        _whisper_download_progress = 1.0
        _whisper_download_message = "Whisper 模型下载完成"
        logger.info(f"Whisper model {size} downloaded successfully")
    except Exception as e:
        _whisper_download_error = str(e)
        logger.error(f"Whisper download failed: {e}")
    finally:
        _whisper_download_running = False


async def _run_download(hf_token: str) -> None:
    """Async wrapper that drives the pyannote download coroutine."""
    svc = get_diarization_service()
    ok = await svc.download(hf_token)
    if ok:
        logger.info("pyannote download complete — loading pipeline …")
        await svc.load_model()
    else:
        logger.error("pyannote download failed")

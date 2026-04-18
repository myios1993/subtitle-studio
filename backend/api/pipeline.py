"""
Pipeline control API endpoints.
Starts/stops the audio capture + ASR pipeline.
"""

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.project import Project
from backend.models.segment import SubtitleSegment
from backend.pipeline.coordinator import (
    start_pipeline as _start_pipeline,
    stop_pipeline as _stop_pipeline,
    get_pipeline,
)

# Track ongoing translation tasks per project (project_id → asyncio.Task)
_translation_tasks: dict[int, asyncio.Task] = {}

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


class PipelineStartRequest(BaseModel):
    mode: Optional[str] = Field(
        None,
        pattern=r"^(microphone|loopback|file)$",
        description="Override capture mode (defaults to project's capture_mode)",
    )
    device_index: Optional[int] = Field(None, description="Audio device index")
    file_path: Optional[str] = Field(None, description="Override file path")
    language: Optional[str] = Field(None, description="Force ASR language (e.g. 'en', 'zh')")
    num_speakers: Optional[int] = Field(None, ge=1, le=20, description="Expected number of speakers (hint for diarization)")
    resume: bool = Field(False, description="Resume from last saved segment (file mode only)")


@router.post("/{project_id}/start")
async def start_pipeline_endpoint(
    project_id: int,
    body: PipelineStartRequest = PipelineStartRequest(),
    db: AsyncSession = Depends(get_db),
):
    """Start the processing pipeline for a project."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.status not in ("idle", "done", "error"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot start pipeline: project status is '{project.status}'",
        )

    # Determine capture mode and file path
    mode = body.mode or project.capture_mode
    file_path = body.file_path or project.source_audio_path or project.source_video_path

    if mode == "file" and not file_path:
        raise HTTPException(
            status_code=400,
            detail="File mode requires a file path (set source_audio_path or source_video_path on the project)",
        )

    try:
        await _start_pipeline(
            project_id=project_id,
            mode=mode,
            device_index=body.device_index,
            file_path=file_path,
            language=body.language,
            num_speakers=body.num_speakers,
            resume=body.resume,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Pipeline failed to start: {type(e).__name__}: {e}",
        )

    return {"message": "Pipeline started", "project_id": project_id, "mode": mode}


@router.post("/{project_id}/stop")
async def stop_pipeline_endpoint(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Stop the processing pipeline for a project."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    pipeline = get_pipeline(project_id)
    if not pipeline or not pipeline.is_running:
        raise HTTPException(
            status_code=409,
            detail="No active pipeline for this project",
        )

    await _stop_pipeline(project_id)
    return {"message": "Pipeline stopped", "project_id": project_id}


class TranslateRequest(BaseModel):
    source_lang: Optional[str] = Field(
        None,
        description="Override source language for this run (e.g. 'en', 'zh'). "
                    "None = use per-segment original_language.",
    )
    segment_ids: Optional[list[int]] = Field(
        None,
        description="Translate only these segment IDs. None = all untranslated.",
    )
    retranslate: bool = Field(
        False,
        description="If True, re-translate already-translated segments too.",
    )


@router.post("/{project_id}/translate")
async def translate_pipeline(
    project_id: int,
    body: TranslateRequest = TranslateRequest(),
    db: AsyncSession = Depends(get_db),
):
    """
    Start translation of segments for a project.
    Runs asynchronously; progress is broadcast via WebSocket
    (translation_progress / translation_done events).
    """
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Prevent duplicate translation tasks
    existing = _translation_tasks.get(project_id)
    if existing and not existing.done():
        raise HTTPException(
            status_code=409,
            detail="Translation already in progress for this project",
        )

    # Capture body fields for use inside the async closure
    _source_lang = body.source_lang
    _segment_ids = body.segment_ids
    _retranslate = body.retranslate

    async def _run_translation():
        from backend.services.translation import TranslationService
        from backend.websocket.hub import ws_manager
        try:
            await ws_manager.broadcast(
                project_id, "translation_started", {"project_id": project_id}
            )
            svc = TranslationService()
            await svc.translate_segments(
                project_id,
                segment_ids=_segment_ids,
                source_lang_override=_source_lang,
                retranslate=_retranslate,
            )
            await ws_manager.broadcast(
                project_id, "translation_done", {"project_id": project_id}
            )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error(
                f"Translation task error for project {project_id}: {exc}", exc_info=True
            )
            from backend.websocket.hub import ws_manager as _ws
            await _ws.broadcast(
                project_id, "translation_error", {"error": str(exc)}
            )
        finally:
            _translation_tasks.pop(project_id, None)

    task = asyncio.create_task(_run_translation())
    _translation_tasks[project_id] = task

    return {"message": "Translation started", "project_id": project_id}


@router.post("/{project_id}/reset")
async def reset_pipeline(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Stop any active pipeline, delete all segments, and reset project
    status to 'idle'.  Use this before re-running a project.
    """
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Stop active pipeline if any
    active = get_pipeline(project_id)
    if active and active.is_running:
        await _stop_pipeline(project_id)

    # Cancel any ongoing translation
    trans_task = _translation_tasks.pop(project_id, None)
    if trans_task and not trans_task.done():
        trans_task.cancel()

    # Delete all segments for this project
    await db.execute(
        delete(SubtitleSegment).where(SubtitleSegment.project_id == project_id)
    )

    # Reset project status
    project.status = "idle"
    await db.commit()

    return {"message": "Project reset", "project_id": project_id}


@router.get("/{project_id}/status")
async def pipeline_status(project_id: int, db: AsyncSession = Depends(get_db)):
    """Get current pipeline status for a project."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    pipeline = get_pipeline(project_id)

    return {
        "project_id": project_id,
        "status": project.status,
        "pipeline_active": pipeline is not None and pipeline.is_running,
    }

"""
Project CRUD API endpoints.
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import _DATA_DIR
from backend.database import get_db
from backend.models.project import Project
from backend.models.segment import SubtitleSegment
from backend.models.speaker import Speaker
from backend.schemas.project import (
    ProjectCreate,
    ProjectUpdate,
    ProjectRead,
    ProjectListRead,
    SpeakerUpdate,
    SpeakerRead,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["projects"])

# ---------------------------------------------------------------------------
# File-type helpers
# ---------------------------------------------------------------------------

_VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".webm"}
_AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac"}


def _classify_upload(filename: str) -> Optional[str]:
    """Return 'video', 'audio', or None for unsupported extensions."""
    ext = Path(filename).suffix.lower()
    if ext in _VIDEO_EXTENSIONS:
        return "video"
    if ext in _AUDIO_EXTENSIONS:
        return "audio"
    return None


async def _write_upload(upload_file: UploadFile, dest: Path) -> None:
    """Write the uploaded file to *dest*, using aiofiles when available."""
    try:
        import aiofiles  # type: ignore[import]

        async with aiofiles.open(dest, "wb") as f:
            while True:
                chunk = await upload_file.read(1024 * 256)  # 256 KiB chunks
                if not chunk:
                    break
                await f.write(chunk)
    except ImportError:
        # Fallback: run blocking writes in thread executor
        loop = asyncio.get_event_loop()
        data = await upload_file.read()
        await loop.run_in_executor(None, dest.write_bytes, data)


async def _prepare_video_background(
    project_id: int, video_path: str, upload_dir: str
) -> None:
    """
    Background task: prepare the video for browser playback, then update the
    project's playback_video_path in the DB.
    """
    from backend.services.audio_capture import prepare_video_for_playback
    from backend.database import async_session_factory

    loop = asyncio.get_event_loop()
    try:
        playback_path: Optional[str] = await loop.run_in_executor(
            None, prepare_video_for_playback, video_path, upload_dir
        )
    except Exception as exc:
        logger.error(f"prepare_video_for_playback failed for project {project_id}: {exc}")
        return

    if not playback_path:
        logger.warning(f"Video preparation returned no output path for project {project_id}")
        return

    try:
        async with async_session_factory() as db:
            project = await db.get(Project, project_id)
            if project:
                project.playback_video_path = playback_path
                await db.commit()
                logger.info(
                    f"Project {project_id}: playback_video_path set to {playback_path}"
                )
    except Exception as exc:
        logger.error(f"Failed to update playback_video_path for project {project_id}: {exc}")


@router.get("", response_model=list[ProjectListRead])
async def list_projects(db: AsyncSession = Depends(get_db)):
    """List all projects with segment counts."""
    stmt = (
        select(
            Project,
            func.count(SubtitleSegment.id).label("segment_count"),
        )
        .outerjoin(SubtitleSegment, SubtitleSegment.project_id == Project.id)
        .group_by(Project.id)
        .order_by(Project.updated_at.desc())
    )
    results = await db.execute(stmt)
    items = []
    for project, seg_count in results.all():
        data = ProjectListRead.model_validate(project)
        data.segment_count = seg_count
        items.append(data)
    return items


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
async def create_project(body: ProjectCreate, db: AsyncSession = Depends(get_db)):
    """Create a new project."""
    project = Project(
        name=body.name,
        capture_mode=body.capture_mode,
        source_audio_path=body.source_audio_path,
        source_video_path=body.source_video_path,
    )
    db.add(project)
    await db.flush()
    await db.refresh(project)
    result = ProjectRead.model_validate(project)
    result.segment_count = 0
    return result


@router.get("/{project_id}", response_model=ProjectRead)
async def get_project(project_id: int, db: AsyncSession = Depends(get_db)):
    """Get a project by ID with its speakers."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    seg_count_stmt = select(func.count(SubtitleSegment.id)).where(
        SubtitleSegment.project_id == project_id
    )
    seg_count = (await db.execute(seg_count_stmt)).scalar() or 0

    result = ProjectRead.model_validate(project)
    result.segment_count = seg_count
    return result


@router.patch("/{project_id}", response_model=ProjectRead)
async def update_project(
    project_id: int, body: ProjectUpdate, db: AsyncSession = Depends(get_db)
):
    """Update project fields."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(project, field, value)

    await db.flush()
    await db.refresh(project)

    seg_count_stmt = select(func.count(SubtitleSegment.id)).where(
        SubtitleSegment.project_id == project_id
    )
    seg_count = (await db.execute(seg_count_stmt)).scalar() or 0

    result = ProjectRead.model_validate(project)
    result.segment_count = seg_count
    return result


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: int, db: AsyncSession = Depends(get_db)):
    """Delete a project and all its segments/speakers (cascade)."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    await db.delete(project)
    await db.commit()


# ---------- File upload ----------


@router.post("/{project_id}/upload", response_model=ProjectRead)
async def upload_project_file(
    project_id: int,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload a source audio or video file and attach it to the project.

    - Video files (.mp4 .mkv .avi .mov .webm): saved as source_video_path; a
      background task remuxes/transcodes the file for browser playback and
      updates playback_video_path when done.
    - Audio files (.mp3 .wav .flac .ogg .m4a): saved as source_audio_path.

    The project row is updated and the updated project is returned immediately
    — video preparation continues in the background.
    """
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if not file.filename:
        raise HTTPException(status_code=400, detail="Uploaded file has no filename")

    file_type = _classify_upload(file.filename)
    if file_type is None:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Unsupported file type '{Path(file.filename).suffix}'. "
                f"Accepted video: {sorted(_VIDEO_EXTENSIONS)}; "
                f"accepted audio: {sorted(_AUDIO_EXTENSIONS)}."
            ),
        )

    # Create per-project upload directory (absolute path to avoid CWD issues)
    upload_dir = Path(_DATA_DIR) / "uploads" / str(project_id)
    upload_dir.mkdir(parents=True, exist_ok=True)

    dest = upload_dir / file.filename
    await _write_upload(file, dest)
    dest_str = str(dest.resolve())  # Always store absolute path

    if file_type == "video":
        project.source_video_path = dest_str
        # Start background preparation — updates playback_video_path when done
        background_tasks.add_task(
            _prepare_video_background, project_id, dest_str, str(upload_dir)
        )
        logger.info(
            f"Project {project_id}: video uploaded to {dest_str}; "
            "playback preparation queued"
        )
    else:  # audio
        project.source_audio_path = dest_str
        logger.info(f"Project {project_id}: audio uploaded to {dest_str}")

    # Commit immediately so the next GET request sees the updated paths
    await db.commit()
    await db.refresh(project)

    seg_count_stmt = select(func.count(SubtitleSegment.id)).where(
        SubtitleSegment.project_id == project_id
    )
    seg_count = (await db.execute(seg_count_stmt)).scalar() or 0

    result = ProjectRead.model_validate(project)
    result.segment_count = seg_count
    return result


# ---------- Speaker sub-routes ----------


@router.patch(
    "/{project_id}/speakers/{speaker_id}",
    response_model=SpeakerRead,
)
async def update_speaker(
    project_id: int,
    speaker_id: str,
    body: SpeakerUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Rename or recolor a speaker. Only updates the Speaker row — all segments
    referencing this speaker_id automatically reflect the change on next read."""
    stmt = select(Speaker).where(
        Speaker.project_id == project_id,
        Speaker.speaker_id == speaker_id,
    )
    result = await db.execute(stmt)
    speaker = result.scalar_one_or_none()
    if not speaker:
        raise HTTPException(status_code=404, detail="Speaker not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(speaker, field, value)

    await db.flush()
    await db.refresh(speaker)
    return SpeakerRead.model_validate(speaker)

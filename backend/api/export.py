"""
SRT export endpoint.
"""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.project import Project
from backend.models.segment import SubtitleSegment
from backend.models.speaker import Speaker

router = APIRouter(prefix="/api/export", tags=["export"])


def ms_to_srt_time(ms: int) -> str:
    """Convert integer milliseconds to SRT time format: HH:MM:SS,mmm"""
    if ms < 0:
        ms = 0
    td = timedelta(milliseconds=ms)
    total_seconds = int(td.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    milliseconds = ms % 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def build_srt(
    segments: list[SubtitleSegment],
    speaker_map: dict[str, str],
    include_speaker: bool = True,
    text_mode: str = "original",
) -> str:
    """
    Build SRT content string.

    text_mode:
      - "original": only original text
      - "translated": only translated text
      - "bilingual": original on first line, translated on second line
    """
    lines: list[str] = []

    for i, seg in enumerate(segments, start=1):
        # Sequence number
        lines.append(str(i))

        # Timestamp line
        lines.append(f"{ms_to_srt_time(seg.start_ms)} --> {ms_to_srt_time(seg.end_ms)}")

        # Speaker prefix
        speaker_prefix = ""
        if include_speaker and seg.speaker_id:
            display_name = speaker_map.get(seg.speaker_id, seg.speaker_id)
            speaker_prefix = f"[{display_name}] "

        # Text line(s)
        if text_mode == "translated" and seg.translated_text:
            lines.append(f"{speaker_prefix}{seg.translated_text}")
        elif text_mode == "bilingual":
            lines.append(f"{speaker_prefix}{seg.original_text}")
            if seg.translated_text:
                lines.append(seg.translated_text)
        else:
            lines.append(f"{speaker_prefix}{seg.original_text}")

        # Blank line separator
        lines.append("")

    return "\n".join(lines)


@router.get("/{project_id}/srt")
async def export_srt(
    project_id: int,
    include_speaker: bool = Query(True, description="Prefix each subtitle with speaker name"),
    text_mode: str = Query("original", pattern=r"^(original|translated|bilingual)$"),
    db: AsyncSession = Depends(get_db),
):
    """Export project subtitles as an SRT file download."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Fetch segments ordered by start_ms
    seg_stmt = (
        select(SubtitleSegment)
        .where(SubtitleSegment.project_id == project_id)
        .order_by(SubtitleSegment.start_ms)
    )
    segments = (await db.execute(seg_stmt)).scalars().all()

    if not segments:
        raise HTTPException(status_code=404, detail="No segments to export")

    # Build speaker display name map
    spk_stmt = select(Speaker).where(Speaker.project_id == project_id)
    speakers = (await db.execute(spk_stmt)).scalars().all()
    speaker_map = {s.speaker_id: (s.label or s.speaker_id) for s in speakers}

    srt_content = build_srt(
        segments=list(segments),
        speaker_map=speaker_map,
        include_speaker=include_speaker,
        text_mode=text_mode,
    )

    filename = f"{project.name}.srt"
    return PlainTextResponse(
        content=srt_content,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )

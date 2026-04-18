"""
SubtitleSegment CRUD API endpoints.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select, func, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.segment import SubtitleSegment
from backend.schemas.segment import (
    SegmentCreate,
    SegmentUpdate,
    SegmentRead,
    SegmentBatchSpeakerUpdate,
)

router = APIRouter(prefix="/api/projects/{project_id}/segments", tags=["segments"])


@router.get("", response_model=list[SegmentRead])
async def list_segments(
    project_id: int,
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    search: str | None = Query(None, description="Filter by text content"),
    db: AsyncSession = Depends(get_db),
):
    """List segments for a project, ordered by start_ms.
    Supports pagination and text search."""
    stmt = (
        select(SubtitleSegment)
        .where(SubtitleSegment.project_id == project_id)
        .order_by(SubtitleSegment.start_ms)
    )

    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(
            SubtitleSegment.original_text.ilike(pattern)
            | SubtitleSegment.translated_text.ilike(pattern)
        )

    stmt = stmt.offset(offset).limit(limit)
    result = await db.execute(stmt)
    segments = result.scalars().all()
    return [SegmentRead.model_validate(s) for s in segments]


@router.get("/count")
async def count_segments(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Return total segment count for a project."""
    stmt = select(func.count(SubtitleSegment.id)).where(
        SubtitleSegment.project_id == project_id
    )
    count = (await db.execute(stmt)).scalar() or 0
    return {"count": count}


@router.post("", response_model=SegmentRead, status_code=status.HTTP_201_CREATED)
async def create_segment(
    project_id: int,
    body: SegmentCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a single segment."""
    # Compute sequence: max existing + 1
    max_seq_stmt = select(func.max(SubtitleSegment.sequence)).where(
        SubtitleSegment.project_id == project_id
    )
    max_seq = (await db.execute(max_seq_stmt)).scalar() or 0

    segment = SubtitleSegment(
        project_id=project_id,
        sequence=max_seq + 1,
        **body.model_dump(),
    )
    db.add(segment)
    await db.flush()
    await db.refresh(segment)
    return SegmentRead.model_validate(segment)


@router.post("/batch", response_model=list[SegmentRead], status_code=status.HTTP_201_CREATED)
async def create_segments_batch(
    project_id: int,
    body: list[SegmentCreate],
    db: AsyncSession = Depends(get_db),
):
    """Create multiple segments at once (used by the ASR pipeline)."""
    max_seq_stmt = select(func.max(SubtitleSegment.sequence)).where(
        SubtitleSegment.project_id == project_id
    )
    max_seq = (await db.execute(max_seq_stmt)).scalar() or 0

    segments = []
    for i, item in enumerate(body, start=1):
        seg = SubtitleSegment(
            project_id=project_id,
            sequence=max_seq + i,
            **item.model_dump(),
        )
        db.add(seg)
        segments.append(seg)

    await db.flush()
    for seg in segments:
        await db.refresh(seg)

    return [SegmentRead.model_validate(s) for s in segments]


@router.patch("/{segment_id}", response_model=SegmentRead)
async def update_segment(
    project_id: int,
    segment_id: int,
    body: SegmentUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a single segment (manual editing)."""
    segment = await db.get(SubtitleSegment, segment_id)
    if not segment or segment.project_id != project_id:
        raise HTTPException(status_code=404, detail="Segment not found")

    update_data = body.model_dump(exclude_unset=True)

    # If user edits text, mark as manually edited
    if "original_text" in update_data or "translated_text" in update_data:
        segment.is_manually_edited = True

    for field, value in update_data.items():
        setattr(segment, field, value)

    await db.commit()
    await db.refresh(segment)
    return SegmentRead.model_validate(segment)


@router.delete("/{segment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_segment(
    project_id: int,
    segment_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a single segment."""
    segment = await db.get(SubtitleSegment, segment_id)
    if not segment or segment.project_id != project_id:
        raise HTTPException(status_code=404, detail="Segment not found")
    await db.delete(segment)
    await db.commit()


@router.post("/bulk-delete", status_code=status.HTTP_200_OK)
async def bulk_delete_segments(
    project_id: int,
    ids: list[int],
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple segments by ID. Returns the count of deleted rows."""
    if not ids:
        return {"deleted": 0}
    result = await db.execute(
        sa_delete(SubtitleSegment).where(
            SubtitleSegment.project_id == project_id,
            SubtitleSegment.id.in_(ids),
        )
    )
    await db.commit()
    return {"deleted": result.rowcount}


@router.post("/batch-update-speakers", status_code=status.HTTP_200_OK)
async def batch_update_speakers(
    project_id: int,
    body: SegmentBatchSpeakerUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Batch update speaker_id for segments (used by diarization backfill)."""
    updated = 0
    for item in body.updates:
        seg = await db.get(SubtitleSegment, item["segment_id"])
        if seg and seg.project_id == project_id:
            seg.speaker_id = item["speaker_id"]
            updated += 1
    await db.commit()
    return {"updated": updated}


@router.post("/merge", response_model=SegmentRead)
async def merge_segments(
    project_id: int,
    segment_ids: list[int],
    db: AsyncSession = Depends(get_db),
):
    """Merge multiple adjacent segments into one.
    Keeps the earliest start_ms and latest end_ms, concatenates text."""
    if len(segment_ids) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 segments to merge")

    segments = []
    for sid in segment_ids:
        seg = await db.get(SubtitleSegment, sid)
        if not seg or seg.project_id != project_id:
            raise HTTPException(status_code=404, detail=f"Segment {sid} not found")
        segments.append(seg)

    # Sort by start_ms
    segments.sort(key=lambda s: s.start_ms)

    # Build merged segment from the first one
    merged = segments[0]
    merged.end_ms = segments[-1].end_ms
    merged.original_text = " ".join(s.original_text for s in segments if s.original_text)
    translated_parts = [s.translated_text for s in segments if s.translated_text]
    merged.translated_text = " ".join(translated_parts) if translated_parts else None
    merged.is_manually_edited = True

    # Delete the rest
    for seg in segments[1:]:
        await db.delete(seg)

    await db.commit()
    await db.refresh(merged)
    return SegmentRead.model_validate(merged)


class SplitRequest(BaseModel):
    split_at_ms: int


def _split_text_at_proportion(text: str, proportion: float) -> tuple[str, str]:
    """Split text at the word boundary closest to the given proportion (0.0–1.0).
    Falls back to splitting at the midpoint of the word list if no good boundary exists."""
    words = text.split()
    if len(words) <= 1:
        # Cannot split by words; do a character-level split at proportion
        cut = max(1, round(len(text) * proportion))
        return text[:cut], text[cut:]

    # Target word index (fractional)
    target_idx = proportion * len(words)

    # Find the nearest word boundary (between word[i-1] and word[i] for i in 1..len-1)
    best_boundary = round(target_idx)
    best_boundary = max(1, min(best_boundary, len(words) - 1))

    first_half = " ".join(words[:best_boundary])
    second_half = " ".join(words[best_boundary:])
    return first_half, second_half


@router.post("/{segment_id}/split")
async def split_segment(
    project_id: int,
    segment_id: int,
    body: SplitRequest,
    db: AsyncSession = Depends(get_db),
):
    """Split a segment at a given millisecond position.

    Returns {"a": SegmentRead, "b": SegmentRead} where:
      - a is the updated original segment (covers start_ms .. split_at_ms)
      - b is the newly created segment  (covers split_at_ms .. end_ms)
    """
    segment = await db.get(SubtitleSegment, segment_id)
    if not segment or segment.project_id != project_id:
        raise HTTPException(status_code=404, detail="Segment not found")

    split_at_ms = body.split_at_ms

    # Validate split point
    if not (segment.start_ms + 50 <= split_at_ms <= segment.end_ms - 50):
        raise HTTPException(
            status_code=400,
            detail=(
                f"split_at_ms must be between {segment.start_ms + 50} "
                f"and {segment.end_ms - 50}"
            ),
        )

    # Proportion of the split within the segment
    duration = segment.end_ms - segment.start_ms
    proportion = (split_at_ms - segment.start_ms) / duration

    # Split original_text
    original_text = segment.original_text or ""
    orig_first, orig_second = _split_text_at_proportion(original_text, proportion)

    # Split translated_text (if present)
    trans_first: str | None = None
    trans_second: str | None = None
    if segment.translated_text:
        trans_first, trans_second = _split_text_at_proportion(segment.translated_text, proportion)

    # Determine sequence for the new segment B: use max_seq + 1
    max_seq_stmt = select(func.max(SubtitleSegment.sequence)).where(
        SubtitleSegment.project_id == project_id
    )
    max_seq = (await db.execute(max_seq_stmt)).scalar() or 0

    # Capture original end before mutating
    original_end_ms = segment.end_ms

    # Update segment A in place
    segment.end_ms = split_at_ms
    segment.original_text = orig_first
    segment.translated_text = trans_first
    segment.is_manually_edited = True

    # Create segment B
    seg_b = SubtitleSegment(
        project_id=project_id,
        sequence=max_seq + 1,
        start_ms=split_at_ms,
        end_ms=original_end_ms,
        original_text=orig_second,
        translated_text=trans_second,
        speaker_id=segment.speaker_id,
        original_language=segment.original_language,
        is_manually_edited=True,
    )
    db.add(seg_b)

    await db.commit()
    await db.refresh(segment)
    await db.refresh(seg_b)

    return {
        "a": SegmentRead.model_validate(segment),
        "b": SegmentRead.model_validate(seg_b),
    }

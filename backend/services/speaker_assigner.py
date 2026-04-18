"""
Speaker assignment service.

Assigns pyannote diarization speaker labels to ASR subtitle segments by
finding the diarization segment with maximum millisecond overlap for each
ASR segment.

Usage:
    assigner = SpeakerAssigner()
    enriched = assigner.assign(asr_segments, diarization_results)
    await backfill_speakers(project_id, diarization_results)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_upsert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import async_session_factory
from backend.models.segment import SubtitleSegment
from backend.models.speaker import Speaker
from backend.websocket.hub import ws_manager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class DiarizationSegment:
    """A single diarization interval produced by pyannote or similar."""

    start_ms: int
    """Start of the interval in integer milliseconds."""

    end_ms: int
    """End of the interval in integer milliseconds."""

    speaker_id: str
    """Raw speaker label, e.g. "SPEAKER_00"."""


# ---------------------------------------------------------------------------
# Overlap helpers
# ---------------------------------------------------------------------------


def _overlap_ms(
    asr_start: int,
    asr_end: int,
    dia_start: int,
    dia_end: int,
) -> int:
    """Return the overlapping milliseconds between two intervals (>=0)."""
    return max(0, min(asr_end, dia_end) - max(asr_start, dia_start))


# ---------------------------------------------------------------------------
# SpeakerAssigner
# ---------------------------------------------------------------------------


class SpeakerAssigner:
    """
    Assigns diarization speaker labels to ASR segments using interval overlap.

    If the ``intervaltree`` package is available it is used for an O(n log n)
    lookup; otherwise a simple linear scan is performed.
    """

    def __init__(self) -> None:
        self._use_intervaltree = self._check_intervaltree()

    # ------------------------------------------------------------------
    # Availability check
    # ------------------------------------------------------------------

    @staticmethod
    def _check_intervaltree() -> bool:
        try:
            import intervaltree  # noqa: F401
            return True
        except ImportError:
            logger.debug(
                "intervaltree not available — using linear scan for speaker assignment. "
                "Install with: pip install intervaltree"
            )
            return False

    # ------------------------------------------------------------------
    # Assignment logic
    # ------------------------------------------------------------------

    def _assign_linear(
        self,
        asr_segments: list[dict],
        diarization: list[DiarizationSegment],
    ) -> list[dict]:
        """O(n*m) linear scan fallback."""
        result: list[dict] = []
        for seg in asr_segments:
            seg = dict(seg)  # shallow copy — do not mutate caller's data
            best_overlap = 0
            best_speaker: Optional[str] = None

            for dia in diarization:
                ov = _overlap_ms(seg["start_ms"], seg["end_ms"], dia.start_ms, dia.end_ms)
                if ov > best_overlap:
                    best_overlap = ov
                    best_speaker = dia.speaker_id

            seg["speaker_id"] = best_speaker
            result.append(seg)

        return result

    def _assign_with_intervaltree(
        self,
        asr_segments: list[dict],
        diarization: list[DiarizationSegment],
    ) -> list[dict]:
        """O(n log n) assignment using intervaltree for candidate lookup."""
        from intervaltree import IntervalTree

        tree: IntervalTree = IntervalTree()
        for dia in diarization:
            # IntervalTree requires begin < end; guard against zero-length intervals
            if dia.end_ms > dia.start_ms:
                tree.addi(dia.start_ms, dia.end_ms, dia)

        result: list[dict] = []
        for seg in asr_segments:
            seg = dict(seg)
            asr_start = seg["start_ms"]
            asr_end = seg["end_ms"]

            candidates = tree.overlap(asr_start, asr_end)
            best_overlap = 0
            best_speaker: Optional[str] = None

            for interval in candidates:
                dia: DiarizationSegment = interval.data
                ov = _overlap_ms(asr_start, asr_end, dia.start_ms, dia.end_ms)
                if ov > best_overlap:
                    best_overlap = ov
                    best_speaker = dia.speaker_id

            seg["speaker_id"] = best_speaker
            result.append(seg)

        return result

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def assign(
        self,
        asr_segments: list[dict],
        diarization: list[DiarizationSegment],
    ) -> list[dict]:
        """
        Match diarization speaker labels to ASR segments.

        For each ASR segment the diarization interval with the greatest
        millisecond overlap is chosen.  If no overlap exists the segment's
        ``speaker_id`` is set to ``None``.

        Args:
            asr_segments:  List of dicts, each must have ``start_ms`` and
                           ``end_ms`` integer fields (milliseconds).
            diarization:   List of :class:`DiarizationSegment` objects.

        Returns:
            New list of dicts (shallow copies) with ``speaker_id`` filled in.
        """
        if not asr_segments or not diarization:
            # Nothing to do — return copies with speaker_id = None
            return [{**seg, "speaker_id": None} for seg in asr_segments]

        if self._use_intervaltree:
            return self._assign_with_intervaltree(asr_segments, diarization)
        return self._assign_linear(asr_segments, diarization)


# ---------------------------------------------------------------------------
# DB back-fill
# ---------------------------------------------------------------------------


async def _upsert_speakers(
    session: AsyncSession,
    project_id: int,
    speaker_ids: list[str],
) -> None:
    """
    Ensure a Speaker row exists for every unique speaker_id in the project.

    Uses SQLite's INSERT OR IGNORE semantics so that existing rows (with user
    labels and colors) are not overwritten.
    """
    for sid in speaker_ids:
        stmt = (
            sqlite_upsert(Speaker)
            .values(project_id=project_id, speaker_id=sid)
            .on_conflict_do_nothing(index_elements=["project_id", "speaker_id"])
        )
        await session.execute(stmt)


async def backfill_speakers(
    project_id: int,
    diarization_results: list[DiarizationSegment],
) -> None:
    """
    Apply diarization speaker labels to all subtitle segments in a project.

    Steps:
      1. Load all SubtitleSegments for the project (ordered by start_ms).
      2. Use :class:`SpeakerAssigner` to compute best-overlap speaker labels.
      3. Upsert every unique speaker_id into the Speaker table.
      4. Persist updated speaker_id values to SubtitleSegment rows.
      5. Broadcast ``segment_updated`` WebSocket events for every changed
         segment.

    Args:
        project_id:          Target project.
        diarization_results: Diarization output from pyannote or similar.
    """
    if not diarization_results:
        logger.info("backfill_speakers: empty diarization for project %d — skipping.", project_id)
        return

    assigner = SpeakerAssigner()

    async with async_session_factory() as session:
        # 1. Load segments
        stmt = (
            select(SubtitleSegment)
            .where(SubtitleSegment.project_id == project_id)
            .order_by(SubtitleSegment.start_ms)
        )
        segments: list[SubtitleSegment] = list(
            (await session.execute(stmt)).scalars().all()
        )

        if not segments:
            logger.info(
                "backfill_speakers: no segments found for project %d.", project_id
            )
            return

        # 2. Build plain dicts for the assigner
        asr_dicts = [
            {"id": seg.id, "start_ms": seg.start_ms, "end_ms": seg.end_ms}
            for seg in segments
        ]
        enriched = assigner.assign(asr_dicts, diarization_results)

        # 3. Upsert speaker rows
        unique_speaker_ids = list(
            {e["speaker_id"] for e in enriched if e["speaker_id"] is not None}
        )
        if unique_speaker_ids:
            await _upsert_speakers(session, project_id, unique_speaker_ids)

        # 4. Map enriched speaker_ids back to ORM objects and persist
        seg_by_id: dict[int, SubtitleSegment] = {seg.id: seg for seg in segments}
        changed: list[SubtitleSegment] = []

        for enriched_seg in enriched:
            seg_id: int = enriched_seg["id"]
            new_speaker: Optional[str] = enriched_seg["speaker_id"]
            orm_seg = seg_by_id.get(seg_id)
            if orm_seg is None:
                continue
            if orm_seg.speaker_id != new_speaker:
                orm_seg.speaker_id = new_speaker
                changed.append(orm_seg)

        await session.commit()

    # 5. Broadcast WebSocket events (outside the session to avoid holding it open)
    for seg in changed:
        await ws_manager.broadcast(
            project_id,
            "segment_updated",
            {
                "id": seg.id,
                "speaker_id": seg.speaker_id,
                "project_id": project_id,
            },
        )

    logger.info(
        "backfill_speakers: updated %d / %d segments for project %d.",
        len(changed), len(segments), project_id,
    )

"""
Pipeline Coordinator — orchestrates the audio→ASR→DB→WebSocket flow.

Manages the lifecycle of:
  1. Audio capture (mic / loopback / file) → AudioBuffer
  2. ASR consumer loop — reads chunks from buffer, runs faster-whisper
  3. Segment deduplication and DB writes
  4. WebSocket broadcast of new segments

Diarization runs as a secondary async task after ASR completes, updating
segments retroactively with speaker labels. Translation is decoupled (Phase 6)
and also runs as a secondary async task.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import numpy as np
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import async_session_factory
from backend.models.segment import SubtitleSegment
from backend.models.project import Project
from backend.pipeline.buffer import AudioBuffer
from backend.services.asr import ASRService, ASRResult
from backend.services.audio_capture import create_capture, CaptureBase
from backend.websocket.hub import ws_manager

logger = logging.getLogger(__name__)

# Thread pool for CPU-bound ASR inference
_asr_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="asr")


def _word_overlap_ratio(text_a: str, text_b: str) -> float:
    """Compute word overlap ratio between two texts (0.0 ~ 1.0)."""
    words_a = set(text_a.lower().split())
    words_b = set(text_b.lower().split())
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    return len(intersection) / min(len(words_a), len(words_b))


class PipelineCoordinator:
    """
    Manages a single project's processing pipeline.

    Usage:
        coordinator = PipelineCoordinator(project_id=1)
        await coordinator.start(mode="file", file_path="/path/to/video.mp4")
        # ... pipeline runs in background ...
        await coordinator.stop()
    """

    def __init__(self, project_id: int):
        self.project_id = project_id
        self._buffer: Optional[AudioBuffer] = None
        self._capture: Optional[CaptureBase] = None
        self._asr: Optional[ASRService] = None
        self._consumer_task: Optional[asyncio.Task] = None
        self._running = False

        # Deduplication state
        self._last_committed_end_ms: int = 0

        # Diarization state
        self._audio_chunks: list[tuple[np.ndarray, int]] = []  # (chunk_audio, offset_ms)
        self._file_path: Optional[str] = None
        self._mode: str = ""
        self._num_speakers: Optional[int] = None

    async def start(
        self,
        mode: str,
        device_index: Optional[int] = None,
        file_path: Optional[str] = None,
        language: Optional[str] = None,
        num_speakers: Optional[int] = None,
        resume: bool = False,
    ) -> None:
        """
        Start the processing pipeline.

        Args:
            mode: "microphone", "loopback", or "file"
            device_index: Audio device index (mic/loopback modes)
            file_path: Path to audio/video file (file mode)
            language: Force ASR language (None = auto-detect)
            num_speakers: Expected number of speakers (hint for diarization)
            resume: If True and mode=="file", seek to the last saved segment's
                    end_ms so only unprocessed audio is transcribed.
        """
        if self._running:
            raise RuntimeError("Pipeline already running")

        logger.info(
            f"Starting pipeline for project {self.project_id} "
            f"(mode={mode}, resume={resume})"
        )

        # Store pipeline parameters for diarization
        self._file_path = file_path
        self._mode = mode
        self._num_speakers = num_speakers
        self._audio_chunks = []

        # Update project status to processing first
        await self._update_project_status("processing")

        try:
            # Initialize ASR model (lazy — first pipeline start loads it)
            if self._asr is None:
                loop = asyncio.get_event_loop()
                self._asr = await loop.run_in_executor(
                    _asr_executor,
                    lambda: ASRService(),
                )

            # For resume mode, determine seek position from last saved segment
            last_end_ms = await self._get_last_segment_end_ms()
            start_offset_ms = 0
            if resume and mode == "file" and last_end_ms > 0:
                # Seek back a little (2s) to avoid missing content at the boundary
                start_offset_ms = max(0, last_end_ms - 2000)
                logger.info(
                    f"Resume mode: seeking to {start_offset_ms}ms "
                    f"(last segment ended at {last_end_ms}ms)"
                )

            # Initialize buffer and capture
            self._buffer = AudioBuffer()
            loop = asyncio.get_event_loop()
            self._capture = create_capture(
                mode=mode,
                buffer=self._buffer,
                loop=loop,
                device_index=device_index,
                file_path=file_path,
                start_offset_ms=start_offset_ms,
            )

            # Set dedup watermark so already-saved segments are not duplicated.
            # In resume mode use last_end_ms we already fetched; otherwise fetch fresh.
            self._last_committed_end_ms = last_end_ms

            self._running = True

            # Start capture (runs in its own thread)
            self._capture.start()

            # Start ASR consumer coroutine
            self._consumer_task = asyncio.create_task(
                self._asr_consumer_loop(language=language)
            )

            await ws_manager.broadcast(
                self.project_id,
                "pipeline_status",
                {"status": "processing", "mode": mode},
            )

        except Exception as exc:
            # Reset DB status so the user can retry
            logger.error(
                f"Pipeline start failed for project {self.project_id}: {exc}",
                exc_info=True,
            )
            await self._update_project_status("error")
            raise

    async def stop(self) -> None:
        """Stop the pipeline gracefully."""
        if not self._running:
            return

        logger.info(f"Stopping pipeline for project {self.project_id}")
        self._running = False

        # Stop capture first
        if self._capture:
            self._capture.stop()

        # Wait for consumer to drain remaining chunks
        if self._consumer_task:
            try:
                await asyncio.wait_for(self._consumer_task, timeout=60)
            except asyncio.TimeoutError:
                logger.warning("ASR consumer did not finish within timeout, cancelling")
                self._consumer_task.cancel()

        # Fire diarization as a background task now that ASR is complete
        asyncio.create_task(self._run_diarization())

        await self._update_project_status("done")
        await ws_manager.broadcast(
            self.project_id,
            "pipeline_done",
            {"project_id": self.project_id},
        )

    async def _asr_consumer_loop(self, language: Optional[str] = None) -> None:
        """
        Main consumer loop: reads audio chunks from the buffer,
        runs ASR, deduplicates, writes to DB, broadcasts via WebSocket.
        """
        loop = asyncio.get_event_loop()
        chunks_processed = 0
        completed_naturally = False  # True when buffer drains (file mode)

        try:
            while True:
                # Get next chunk from buffer
                result = await self._buffer.get_chunk()

                if result is None:
                    if self._buffer.is_finished:
                        logger.info("Buffer finished — ASR consumer exiting")
                        completed_naturally = True
                        break
                    if not self._running:
                        # Check if there's still data to drain
                        if self._buffer.is_finished:
                            completed_naturally = True
                            break
                    continue

                chunk_audio, chunk_offset_ms = result
                # Accumulate raw audio for diarization
                self._audio_chunks.append((chunk_audio, chunk_offset_ms))
                chunks_processed += 1

                logger.info(
                    f"Processing chunk #{chunks_processed}: "
                    f"offset={chunk_offset_ms}ms, samples={len(chunk_audio)}"
                )

                # Run ASR in thread pool (CPU-bound)
                asr_result: ASRResult = await loop.run_in_executor(
                    _asr_executor,
                    lambda: self._asr.transcribe_chunk(
                        chunk_audio, chunk_offset_ms, language
                    ),
                )

                if not asr_result.segments:
                    logger.debug(f"Chunk #{chunks_processed}: no speech detected")
                    continue

                # Deduplicate and write to DB
                new_segments = await self._deduplicate_and_save(asr_result)

                if new_segments:
                    # Broadcast new segments via WebSocket
                    for seg_data in new_segments:
                        await ws_manager.broadcast(
                            self.project_id,
                            "segment_created",
                            seg_data,
                        )

                # Broadcast progress
                total_duration = self._buffer.total_duration_ms
                if total_duration > 0:
                    progress = min(
                        (chunk_offset_ms + asr_result.duration_ms) / total_duration,
                        1.0,
                    )
                else:
                    progress = 0.0

                await ws_manager.broadcast(
                    self.project_id,
                    "pipeline_status",
                    {
                        "status": "processing",
                        "progress": round(progress, 3),
                        "chunks_processed": chunks_processed,
                    },
                )

        except asyncio.CancelledError:
            logger.info("ASR consumer cancelled")
        except Exception as e:
            logger.error(f"ASR consumer error: {e}", exc_info=True)
            self._running = False
            await self._update_project_status("error")
            # Remove from active registry so the project can be retried
            _active_pipelines.pop(self.project_id, None)
            await ws_manager.broadcast(
                self.project_id,
                "pipeline_error",
                {"error": str(e)},
            )
        finally:
            logger.info(f"ASR consumer finished: {chunks_processed} chunks processed")
            if completed_naturally:
                # File mode: schedule completion as a separate task to avoid
                # deadlock (stop() waits for this task; we can't call stop() here).
                asyncio.create_task(self._complete_pipeline())

    async def _complete_pipeline(self) -> None:
        """
        Called when a file-mode pipeline drains its buffer and ASR finishes
        naturally (without the user clicking Stop).

        Does the same post-processing as stop() but without waiting on
        the consumer task (which would deadlock, since we're called from it).
        """
        if not self._running:
            return  # Manual stop() already handled this

        logger.info(f"Pipeline for project {self.project_id} completed naturally")
        self._running = False

        # Stop capture thread (file capture is already done, this is a no-op)
        if self._capture:
            self._capture.stop()

        # Remove from global registry so the project can be re-run
        _active_pipelines.pop(self.project_id, None)

        # Fire diarization as a background task
        asyncio.create_task(self._run_diarization())

        # Update DB and notify frontend
        await self._update_project_status("done")
        await ws_manager.broadcast(
            self.project_id,
            "pipeline_done",
            {"project_id": self.project_id},
        )

    async def _load_full_audio(self) -> Optional[np.ndarray]:
        """
        Load the full audio for diarization.
        - file mode: decode from source file using ffmpeg
        - mic/loopback: reconstruct from accumulated chunks (non-overlapping steps)
        """
        import subprocess
        from backend.config import settings as _settings

        loop = asyncio.get_event_loop()

        if self._mode == "file" and self._file_path:
            # Decode fresh from file — most accurate for diarization
            def _decode():
                cmd = [
                    _settings.ffmpeg_path,
                    "-i", self._file_path,
                    "-vn",
                    "-ar", str(_settings.audio_sample_rate),
                    "-ac", "1",
                    "-f", "f32le",
                    "-loglevel", "error",
                    "pipe:1",
                ]
                proc = subprocess.run(cmd, capture_output=True, timeout=3600)
                if proc.returncode != 0:
                    raise RuntimeError(f"ffmpeg decode failed: {proc.stderr.decode()[:200]}")
                return np.frombuffer(proc.stdout, dtype=np.float32)

            try:
                audio = await loop.run_in_executor(None, _decode)
                logger.info(
                    f"Loaded full audio from file: {len(audio)} samples "
                    f"({len(audio) / 16000:.1f}s)"
                )
                return audio
            except Exception as e:
                logger.error(f"Failed to load audio from file: {e}")
                return None

        elif self._audio_chunks:
            # Reconstruct from accumulated chunks, taking non-overlapping steps
            step_samples = int(
                (settings.audio_chunk_duration_s - settings.audio_overlap_duration_s)
                * settings.audio_sample_rate
            )

            parts = []
            for i, (chunk, _offset) in enumerate(self._audio_chunks):
                if i < len(self._audio_chunks) - 1:
                    parts.append(chunk[:step_samples])
                else:
                    parts.append(chunk)

            audio = np.concatenate(parts)
            logger.info(
                f"Reconstructed audio from {len(self._audio_chunks)} chunks: "
                f"{len(audio) / 16000:.1f}s"
            )
            return audio

        return None

    async def _run_diarization(self) -> None:
        """
        Run speaker diarization on the full project audio and backfill speaker
        labels on all segments. Runs as a fire-and-forget task.
        """
        from backend.services.diarization import get_diarization_service
        from backend.services.speaker_assigner import backfill_speakers

        svc = get_diarization_service()

        if not svc.is_downloaded:
            logger.info(
                "pyannote models not downloaded — skipping diarization. "
                "Download them via the Settings page."
            )
            return

        if not svc._loaded:
            logger.info("Loading pyannote pipeline for diarization …")
            ok = await svc.load_model()
            if not ok:
                logger.warning("Failed to load pyannote — skipping diarization")
                return

        audio = await self._load_full_audio()
        if audio is None or len(audio) < 16000:  # skip if < 1s
            logger.warning("No audio available for diarization")
            return

        logger.info(f"Running diarization on {len(audio) / 16000:.1f}s audio …")

        await ws_manager.broadcast(
            self.project_id,
            "pipeline_status",
            {"status": "diarizing", "progress": 0},
        )

        loop = asyncio.get_event_loop()
        from concurrent.futures import ThreadPoolExecutor
        _dia_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="diarize")

        try:
            diarization_results = await loop.run_in_executor(
                _dia_executor,
                lambda: svc.diarize(
                    audio,
                    sample_rate=16000,
                    num_speakers=self._num_speakers,
                ),
            )
        except Exception as e:
            logger.error(f"Diarization failed: {e}", exc_info=True)
            return

        if not diarization_results:
            logger.info("Diarization returned no segments")
            return

        logger.info(f"Diarization complete: {len(diarization_results)} speaker intervals")

        try:
            await backfill_speakers(self.project_id, diarization_results)
            await ws_manager.broadcast(
                self.project_id,
                "diarization_done",
                {"project_id": self.project_id, "num_intervals": len(diarization_results)},
            )
            logger.info("Speaker backfill complete")
        except Exception as e:
            logger.error(f"Speaker backfill failed: {e}", exc_info=True)

    async def _deduplicate_and_save(self, asr_result: ASRResult) -> list[dict]:
        """
        Filter out duplicate segments from overlapping chunks,
        write new ones to DB, and return their serialized dicts.

        Dedup strategy (two layers):
          1. Time filter: skip segments ending before the high watermark
          2. Text similarity: for segments in the overlap zone, compare with
             recent DB segments using word overlap ratio
        """
        new_segment_dicts: list[dict] = []

        async with async_session_factory() as db:
            # Fetch last N segments from DB for text-based dedup
            recent_stmt = (
                select(SubtitleSegment)
                .where(SubtitleSegment.project_id == self.project_id)
                .order_by(SubtitleSegment.start_ms.desc())
                .limit(10)
            )
            recent_result = await db.execute(recent_stmt)
            recent_segments = list(recent_result.scalars().all())

            # Get current max sequence
            max_seq_stmt = select(func.max(SubtitleSegment.sequence)).where(
                SubtitleSegment.project_id == self.project_id
            )
            max_seq = (await db.execute(max_seq_stmt)).scalar() or 0
            seq_counter = max_seq

            for asr_seg in asr_result.segments:
                # Skip empty text
                if not asr_seg.text.strip():
                    continue

                # Layer 1: Time-based filter
                # Skip segments that end well before the high watermark
                if asr_seg.end_ms < self._last_committed_end_ms - 200:
                    continue

                # Layer 2: Text similarity for segments in the overlap zone
                in_overlap_zone = (
                    asr_seg.start_ms < self._last_committed_end_ms + 200
                )

                if in_overlap_zone and recent_segments:
                    is_duplicate = False
                    for existing in recent_segments:
                        ratio = _word_overlap_ratio(asr_seg.text, existing.original_text)
                        if ratio > 0.7:
                            logger.debug(
                                f"Dedup: skipping '{asr_seg.text[:40]}...' "
                                f"(overlap={ratio:.2f} with '{existing.original_text[:40]}...')"
                            )
                            is_duplicate = True
                            break
                    if is_duplicate:
                        continue

                # New segment — write to DB
                seq_counter += 1
                db_seg = SubtitleSegment(
                    project_id=self.project_id,
                    sequence=seq_counter,
                    start_ms=asr_seg.start_ms,
                    end_ms=asr_seg.end_ms,
                    original_text=asr_seg.text,
                    original_language=asr_seg.language,
                    confidence=asr_seg.confidence,
                    speaker_id=None,  # filled in by diarization later
                )
                db.add(db_seg)
                await db.flush()
                await db.refresh(db_seg)

                seg_dict = {
                    "id": db_seg.id,
                    "project_id": db_seg.project_id,
                    "sequence": db_seg.sequence,
                    "start_ms": db_seg.start_ms,
                    "end_ms": db_seg.end_ms,
                    "original_text": db_seg.original_text,
                    "translated_text": None,
                    "original_language": db_seg.original_language,
                    "speaker_id": None,
                    "is_manually_edited": False,
                    "confidence": db_seg.confidence,
                }
                new_segment_dicts.append(seg_dict)

                # Update high watermark
                if db_seg.end_ms > self._last_committed_end_ms:
                    self._last_committed_end_ms = db_seg.end_ms

                # Also append to recent_segments for subsequent dedup within same chunk
                recent_segments.insert(0, db_seg)
                if len(recent_segments) > 10:
                    recent_segments.pop()

            await db.commit()

        if new_segment_dicts:
            logger.info(
                f"Saved {len(new_segment_dicts)} new segments "
                f"(watermark={self._last_committed_end_ms}ms)"
            )

        return new_segment_dicts

    async def _translate_new_segments(self, segment_ids: list[int]) -> None:
        """
        Trigger translation for a batch of freshly saved segments.

        Runs as a fire-and-forget asyncio task (created with
        asyncio.create_task).  All failures are caught and logged — this
        method must never propagate an exception that could crash the caller.

        Args:
            segment_ids: Primary keys of SubtitleSegment rows to translate.
        """
        if not segment_ids:
            return

        try:
            from backend.services.translation import TranslationService  # type: ignore[import]
        except ImportError:
            logger.warning(
                "backend.services.translation is not available — "
                "skipping post-segment translation."
            )
            return

        try:
            service = TranslationService()
            await service.translate_segments(self.project_id, segment_ids)
        except Exception as exc:
            # Translation failures are non-fatal: log and move on.
            logger.warning(
                f"Translation task failed for segment IDs {segment_ids}: {exc}",
                exc_info=True,
            )

    async def _get_last_segment_end_ms(self) -> int:
        """Get the end_ms of the last segment for this project (for resuming)."""
        async with async_session_factory() as db:
            stmt = (
                select(func.max(SubtitleSegment.end_ms))
                .where(SubtitleSegment.project_id == self.project_id)
            )
            result = (await db.execute(stmt)).scalar()
            return result or 0

    async def _update_project_status(self, status: str) -> None:
        """Update the project status in the DB."""
        async with async_session_factory() as db:
            project = await db.get(Project, self.project_id)
            if project:
                project.status = status
                await db.commit()

    @property
    def is_running(self) -> bool:
        return self._running


# ---------------------------------------------------------------------------
# Global registry of active pipelines (one per project)
# ---------------------------------------------------------------------------

_active_pipelines: dict[int, PipelineCoordinator] = {}


async def start_pipeline(
    project_id: int,
    mode: str,
    device_index: Optional[int] = None,
    file_path: Optional[str] = None,
    language: Optional[str] = None,
    num_speakers: Optional[int] = None,
    resume: bool = False,
) -> PipelineCoordinator:
    """Start a pipeline for a project. Returns the coordinator."""
    if project_id in _active_pipelines:
        raise RuntimeError(f"Pipeline already running for project {project_id}")

    coordinator = PipelineCoordinator(project_id)
    _active_pipelines[project_id] = coordinator

    try:
        await coordinator.start(
            mode=mode,
            device_index=device_index,
            file_path=file_path,
            language=language,
            num_speakers=num_speakers,
            resume=resume,
        )
    except Exception:
        # Clean up so the project doesn't get stuck
        _active_pipelines.pop(project_id, None)
        raise
    return coordinator


async def stop_pipeline(project_id: int) -> None:
    """Stop and remove the pipeline for a project."""
    coordinator = _active_pipelines.pop(project_id, None)
    if coordinator:
        await coordinator.stop()


def get_pipeline(project_id: int) -> Optional[PipelineCoordinator]:
    """Get the active pipeline for a project, if any."""
    return _active_pipelines.get(project_id)

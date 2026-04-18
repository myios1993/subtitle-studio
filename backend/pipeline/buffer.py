"""
Sliding-window audio buffer.

Accumulates raw PCM audio samples (float32, 16kHz, mono) and yields
fixed-duration chunks with configurable overlap to prevent word splitting
at chunk boundaries.

Data flow:
  AudioCapture --push_samples()--> AudioBuffer --get_chunk()--> ASR pipeline
"""

import asyncio
import logging
from typing import Optional

import numpy as np

from backend.config import settings

logger = logging.getLogger(__name__)


class AudioBuffer:
    """
    Thread-safe sliding-window buffer for audio chunks.

    Parameters:
        sample_rate:      Expected sample rate (default 16000 Hz)
        chunk_duration_s: Duration of each output chunk in seconds (default 30)
        overlap_s:        Overlap between consecutive chunks in seconds (default 2)
    """

    def __init__(
        self,
        sample_rate: int = settings.audio_sample_rate,
        chunk_duration_s: int = settings.audio_chunk_duration_s,
        overlap_s: int = settings.audio_overlap_duration_s,
    ):
        self.sample_rate = sample_rate
        self.chunk_size = chunk_duration_s * sample_rate      # 30s * 16000 = 480000 samples
        self.overlap_size = overlap_s * sample_rate           # 2s  * 16000 =  32000 samples
        self.step_size = self.chunk_size - self.overlap_size   # 28s = 448000 samples

        # Internal accumulation buffer
        self._buffer = np.array([], dtype=np.float32)
        self._lock = asyncio.Lock()

        # Chunk output queue — downstream consumers await get_chunk()
        self._chunk_queue: asyncio.Queue[tuple[np.ndarray, int]] = asyncio.Queue(maxsize=10)

        # Tracks the absolute sample offset of the next chunk's start
        # Used to compute chunk_offset_ms for timestamp alignment
        self._next_chunk_start_sample: int = 0

        # Total samples received so far
        self._total_samples: int = 0

        self._finished = False

    async def push_samples(self, samples: np.ndarray) -> None:
        """
        Push new audio samples into the buffer.
        Automatically emits full chunks to the output queue when enough
        data has accumulated.

        Args:
            samples: float32 numpy array of mono audio samples at self.sample_rate
        """
        async with self._lock:
            self._buffer = np.concatenate([self._buffer, samples])
            self._total_samples += len(samples)

            # Emit as many full chunks as available
            while len(self._buffer) >= self.chunk_size:
                chunk = self._buffer[:self.chunk_size].copy()
                chunk_offset_ms = int(self._next_chunk_start_sample / self.sample_rate * 1000)

                await self._chunk_queue.put((chunk, chunk_offset_ms))

                # Slide the window forward by step_size (chunk_size - overlap)
                self._buffer = self._buffer[self.step_size:]
                self._next_chunk_start_sample += self.step_size

                logger.debug(
                    f"Chunk emitted: offset={chunk_offset_ms}ms, "
                    f"buffer_remaining={len(self._buffer)} samples"
                )

    async def flush(self) -> None:
        """
        Flush any remaining audio in the buffer as a final (possibly shorter) chunk.
        Call this when audio capture ends to process the tail.
        """
        async with self._lock:
            if len(self._buffer) > 0:
                # Pad short chunks to minimum viable length (1 second)
                min_samples = self.sample_rate  # 1 second minimum
                if len(self._buffer) < min_samples:
                    # Too short for meaningful ASR — pad with silence
                    self._buffer = np.pad(
                        self._buffer,
                        (0, min_samples - len(self._buffer)),
                        mode="constant",
                        constant_values=0.0,
                    )

                chunk = self._buffer.copy()
                chunk_offset_ms = int(self._next_chunk_start_sample / self.sample_rate * 1000)
                await self._chunk_queue.put((chunk, chunk_offset_ms))
                self._buffer = np.array([], dtype=np.float32)

                logger.debug(
                    f"Final chunk flushed: offset={chunk_offset_ms}ms, "
                    f"length={len(chunk)} samples"
                )

            self._finished = True

    async def get_chunk(self) -> Optional[tuple[np.ndarray, int]]:
        """
        Await the next audio chunk from the buffer.

        Returns:
            Tuple of (chunk_audio: np.ndarray, chunk_offset_ms: int),
            or None if the buffer is finished and empty.
        """
        if self._finished and self._chunk_queue.empty():
            return None

        try:
            return await asyncio.wait_for(self._chunk_queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            if self._finished and self._chunk_queue.empty():
                return None
            return None  # Caller should retry

    @property
    def is_finished(self) -> bool:
        return self._finished and self._chunk_queue.empty()

    @property
    def total_duration_ms(self) -> int:
        """Total audio duration received so far in milliseconds."""
        return int(self._total_samples / self.sample_rate * 1000)

    def reset(self) -> None:
        """Reset the buffer to initial state."""
        self._buffer = np.array([], dtype=np.float32)
        self._chunk_queue = asyncio.Queue(maxsize=10)
        self._next_chunk_start_sample = 0
        self._total_samples = 0
        self._finished = False

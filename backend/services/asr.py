"""
ASR (Automatic Speech Recognition) service — faster-whisper wrapper.

Transcribes audio chunks (float32, 16kHz, mono) and returns structured
segments with word-level timestamps.
"""

import logging
from typing import Optional

import numpy as np

from backend.config import settings

logger = logging.getLogger(__name__)


class ASRSegment:
    """Structured output from ASR."""
    __slots__ = ("start_ms", "end_ms", "text", "language", "confidence", "words")

    def __init__(
        self,
        start_ms: int,
        end_ms: int,
        text: str,
        language: str = "",
        confidence: float = 0.0,
        words: list[dict] | None = None,
    ):
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.text = text
        self.language = language
        self.confidence = confidence
        self.words = words or []

    def to_dict(self) -> dict:
        return {
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "text": self.text,
            "language": self.language,
            "confidence": self.confidence,
            "words": self.words,
        }


class ASRResult:
    """Result of transcribing one audio chunk."""
    __slots__ = ("segments", "language", "language_probability", "duration_ms")

    def __init__(
        self,
        segments: list[ASRSegment],
        language: str,
        language_probability: float,
        duration_ms: int,
    ):
        self.segments = segments
        self.language = language
        self.language_probability = language_probability
        self.duration_ms = duration_ms


class ASRService:
    """
    Whisper-based ASR using faster-whisper (CTranslate2 backend).

    Loads the model once on init. Transcription is CPU-bound and should be
    called from a thread pool to avoid blocking the event loop.
    """

    def __init__(
        self,
        model_size: str = settings.whisper_model_size,
        device: str = "auto",
        compute_type: str = "auto",
        model_dir: str = settings.whisper_model_dir,
    ):
        """
        Args:
            model_size: Whisper model size (tiny, base, small, medium, large-v3, distil-large-v3)
            device: "cpu", "cuda", or "auto" (auto-detect)
            compute_type: "int8", "float16", "float32", or "auto"
            model_dir: Directory to download/cache model files
        """
        from faster_whisper import WhisperModel

        # Resolve device: try CUDA with real load test, fall back to CPU.
        # ctranslate2.get_supported_compute_types("cuda") only checks compile-time
        # support, not whether CUDA runtime DLLs (cublas, cudnn) are actually installed.
        if device == "auto":
            loaded = False
            for try_device, try_compute in [("cuda", "float16"), ("cpu", "int8")]:
                try:
                    logger.info(f"Trying Whisper model: {model_size} (device={try_device}, compute_type={try_compute})")
                    self._model = WhisperModel(
                        model_size,
                        device=try_device,
                        compute_type=try_compute,
                        download_root=model_dir,
                    )
                    # Verify it actually works with a tiny inference
                    _test_audio = np.zeros(16000, dtype=np.float32)  # 1s silence
                    list(self._model.transcribe(_test_audio, language="en")[0])
                    logger.info(f"Whisper model loaded: {model_size} on {try_device} ({try_compute})")
                    loaded = True
                    break
                except Exception as e:
                    logger.warning(f"Failed to load on {try_device}: {e}")
                    continue
            if not loaded:
                raise RuntimeError("Could not load Whisper model on any device")
        else:
            resolved_compute = compute_type if compute_type != "auto" else "int8"
            logger.info(f"Loading Whisper model: {model_size} (device={device}, compute_type={resolved_compute})")
            self._model = WhisperModel(
                model_size,
                device=device,
                compute_type=resolved_compute,
                download_root=model_dir,
            )
            logger.info(f"Whisper model loaded: {model_size}")

        self._model_size = model_size

    def transcribe_chunk(
        self,
        audio: np.ndarray,
        chunk_offset_ms: int = 0,
        language: Optional[str] = None,
    ) -> ASRResult:
        """
        Transcribe a single audio chunk.

        Args:
            audio: float32 numpy array, 16kHz mono
            chunk_offset_ms: Absolute time offset of this chunk's start (ms).
                             All segment timestamps will be shifted by this value.
            language: Force language (e.g. "en", "zh"). None = auto-detect.

        Returns:
            ASRResult with segments containing absolute timestamps.
        """
        # Provide an initial prompt to bias Whisper toward Simplified Chinese
        # when the target language is Chinese or unknown (auto-detect).
        # This prevents Whisper from defaulting to Traditional Chinese for Mandarin.
        if language in (None, "zh", "zh-CN"):
            initial_prompt = (
                "以下是普通话的转录，使用简体中文，"
                "不使用繁体字。"
            )
        else:
            initial_prompt = None

        segments_gen, info = self._model.transcribe(
            audio,
            language=language,
            word_timestamps=True,
            vad_filter=True,
            initial_prompt=initial_prompt,
            vad_parameters={
                "min_silence_duration_ms": 500,
                "speech_pad_ms": 200,
            },
        )

        segments: list[ASRSegment] = []

        for seg in segments_gen:
            # Convert relative timestamps (within chunk) to absolute ms
            abs_start_ms = chunk_offset_ms + int(seg.start * 1000)
            abs_end_ms = chunk_offset_ms + int(seg.end * 1000)

            # Compute average confidence from words
            words = []
            total_prob = 0.0
            word_count = 0
            if seg.words:
                for w in seg.words:
                    words.append({
                        "word": w.word,
                        "start_ms": chunk_offset_ms + int(w.start * 1000),
                        "end_ms": chunk_offset_ms + int(w.end * 1000),
                        "probability": round(w.probability, 3),
                    })
                    total_prob += w.probability
                    word_count += 1

            avg_confidence = (total_prob / word_count) if word_count > 0 else 0.0

            asr_seg = ASRSegment(
                start_ms=abs_start_ms,
                end_ms=abs_end_ms,
                text=seg.text.strip(),
                language=info.language,
                confidence=round(avg_confidence, 3),
                words=words,
            )
            segments.append(asr_seg)

        duration_ms = int(len(audio) / settings.audio_sample_rate * 1000)

        result = ASRResult(
            segments=segments,
            language=info.language,
            language_probability=round(info.language_probability, 3),
            duration_ms=duration_ms,
        )

        logger.info(
            f"ASR: {len(segments)} segments from {duration_ms}ms audio "
            f"(lang={info.language}, prob={info.language_probability:.2f})"
        )

        return result

    @property
    def model_size(self) -> str:
        return self._model_size

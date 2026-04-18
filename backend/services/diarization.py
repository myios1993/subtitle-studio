"""
Diarization service — pyannote.audio 3.1 wrapper for speaker diarization.

Local-first design:
  - On first use: download models to models/pyannote/ using a HuggingFace token.
  - On subsequent uses: load entirely from local cache, NO internet, NO token.

The three sub-models that get cached locally:
  • pyannote/speaker-diarization-3.1  (pipeline config + weights)
  • pyannote/segmentation-3.0          (VAD / segmentation backbone)
  • speechbrain/spkrec-ecapa-voxceleb  (speaker embedding backbone)

Usage:
    service = get_diarization_service()

    # First-time setup (download):
    success = await service.download(hf_token="hf_xxx", progress_cb=lambda p, msg: ...)

    # Every run (local):
    await service.load_model()          # no token needed if already downloaded
    segments = service.diarize(audio_np, sample_rate=16000)
"""

from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from backend.config import settings

logger = logging.getLogger(__name__)

# Thread pool dedicated to pyannote inference and download (CPU/GPU/IO-bound)
_diarization_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="diarization")

# The three HuggingFace model repos that the diarization pipeline depends on
_PIPELINE_REPO = "pyannote/speaker-diarization-3.1"
_REQUIRED_REPOS = [
    "pyannote/speaker-diarization-3.1",
    "pyannote/segmentation-3.0",
    "speechbrain/spkrec-ecapa-voxceleb",
]


@dataclass
class DiarizationSegment:
    """A single speaker-attributed time segment from pyannote diarization."""
    start_ms: int
    end_ms: int
    speaker_id: str  # e.g. "SPEAKER_00", "SPEAKER_01"


# ---------------------------------------------------------------------------
# Local cache helpers
# ---------------------------------------------------------------------------

def _repo_to_cache_dir(repo_id: str, cache_dir: str) -> Path:
    """
    HuggingFace caches repos as  {cache_dir}/models--{org}--{name}/
    e.g. "pyannote/speaker-diarization-3.1"
         → models--pyannote--speaker-diarization-3.1
    """
    folder_name = "models--" + repo_id.replace("/", "--")
    return Path(cache_dir) / folder_name


def _repo_is_cached(repo_id: str, cache_dir: str) -> bool:
    """Return True if the repo's snapshot directory exists and is non-empty."""
    repo_path = _repo_to_cache_dir(repo_id, cache_dir)
    snapshots_dir = repo_path / "snapshots"
    if not snapshots_dir.exists():
        return False
    # Check that at least one snapshot sub-directory contains files
    for snap in snapshots_dir.iterdir():
        if snap.is_dir() and any(snap.iterdir()):
            return True
    return False


def is_pyannote_downloaded(cache_dir: Optional[str] = None) -> bool:
    """Return True only when ALL required sub-models are cached locally."""
    cache_dir = cache_dir or settings.pyannote_model_dir
    return all(_repo_is_cached(repo, cache_dir) for repo in _REQUIRED_REPOS)


def get_pyannote_download_status(cache_dir: Optional[str] = None) -> dict:
    """Return per-repo download status."""
    cache_dir = cache_dir or settings.pyannote_model_dir
    status = {}
    for repo in _REQUIRED_REPOS:
        status[repo] = _repo_is_cached(repo, cache_dir)
    return {
        "repos": status,
        "all_downloaded": all(status.values()),
        "cache_dir": cache_dir,
    }


# ---------------------------------------------------------------------------
# Download helper (blocking — run in thread executor)
# ---------------------------------------------------------------------------

def _download_all_repos(
    hf_token: str,
    cache_dir: str,
    progress_cb: Optional[Callable[[float, str], None]] = None,
) -> bool:
    """
    Download all three required repos to cache_dir.
    progress_cb(fraction 0-1, message) is called at each step.
    Returns True on success, False on any error.
    """
    try:
        from huggingface_hub import snapshot_download  # type: ignore[import]
    except ImportError:
        logger.error("huggingface_hub is not installed. Run: pip install huggingface-hub")
        return False

    os.makedirs(cache_dir, exist_ok=True)
    n = len(_REQUIRED_REPOS)

    for i, repo_id in enumerate(_REQUIRED_REPOS):
        step_start = i / n
        step_end = (i + 1) / n

        if _repo_is_cached(repo_id, cache_dir):
            msg = f"✓ {repo_id} (已缓存，跳过)"
            logger.info(msg)
            if progress_cb:
                progress_cb(step_end, msg)
            continue

        msg = f"⬇ 正在下载 {repo_id} …"
        logger.info(msg)
        if progress_cb:
            progress_cb(step_start + 0.02, msg)

        try:
            snapshot_download(
                repo_id=repo_id,
                token=hf_token,
                cache_dir=cache_dir,
                # Allow partial downloads to resume
                ignore_patterns=["*.msgpack", "*.h5", "flax_model*", "tf_model*"],
            )
            msg = f"✓ {repo_id} 下载完成"
            logger.info(msg)
            if progress_cb:
                progress_cb(step_end, msg)

        except Exception as exc:
            msg = f"✗ {repo_id} 下载失败: {exc}"
            logger.error(msg, exc_info=True)
            if progress_cb:
                progress_cb(step_start, msg)
            return False

    return True


# ---------------------------------------------------------------------------
# DiarizationService
# ---------------------------------------------------------------------------

class DiarizationService:
    """
    Wraps pyannote.audio's SpeakerDiarization pipeline.

    Local-first:  if models are already in cache_dir, loads without any token.
    Remote fallback: call download(hf_token) first to cache models locally.
    """

    def __init__(self, model_dir: str = settings.pyannote_model_dir) -> None:
        self._model_dir = model_dir
        self._pipeline = None
        self._load_lock = asyncio.Lock()
        self._loaded = False

        # Download progress state (polled by the API)
        self.download_progress: float = 0.0      # 0.0 – 1.0
        self.download_message: str = ""
        self.download_running: bool = False
        self.download_error: str = ""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_downloaded(self) -> bool:
        return is_pyannote_downloaded(self._model_dir)

    async def load_model(self, hf_token: Optional[str] = None) -> bool:
        """
        Load the diarization pipeline.

        If models are cached locally: loads offline, no token needed.
        If models are NOT cached: returns False (call download() first).

        Returns True on success.
        """
        async with self._load_lock:
            if self._loaded:
                return True

            if not self.is_downloaded:
                if hf_token:
                    # Opportunistically download first
                    ok = await self.download(hf_token)
                    if not ok:
                        return False
                else:
                    logger.warning(
                        "pyannote models not found locally. "
                        "Call download(hf_token) first or run the model download wizard."
                    )
                    return False

            logger.info("Loading pyannote pipeline from local cache …")
            loop = asyncio.get_event_loop()
            try:
                pipeline = await loop.run_in_executor(
                    _diarization_executor,
                    self._load_pipeline_local,
                )
                if pipeline is not None:
                    self._pipeline = pipeline
                    self._loaded = True
                    logger.info("pyannote diarization pipeline loaded (offline)")
                    return True
                return False
            except Exception as exc:
                logger.error(f"Failed to load pyannote pipeline: {exc}", exc_info=True)
                return False

    async def download(
        self,
        hf_token: str,
        progress_cb: Optional[Callable[[float, str], None]] = None,
    ) -> bool:
        """
        Download all pyannote sub-models to local cache.
        Sets self.download_progress / download_message while running.
        Returns True on success.
        """
        if self.download_running:
            logger.warning("Download already in progress")
            return False

        self.download_running = True
        self.download_progress = 0.0
        self.download_message = "开始下载 …"
        self.download_error = ""

        def _cb(fraction: float, msg: str) -> None:
            self.download_progress = fraction
            self.download_message = msg
            if progress_cb:
                progress_cb(fraction, msg)

        loop = asyncio.get_event_loop()
        try:
            ok = await loop.run_in_executor(
                _diarization_executor,
                lambda: _download_all_repos(hf_token, self._model_dir, _cb),
            )
            if ok:
                self.download_progress = 1.0
                self.download_message = "所有模型下载完成 ✓"
            else:
                self.download_error = self.download_message  # last error message
            return ok
        except Exception as exc:
            self.download_error = str(exc)
            logger.error(f"Download task error: {exc}", exc_info=True)
            return False
        finally:
            self.download_running = False

    def diarize(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
        num_speakers: Optional[int] = None,
    ) -> list[DiarizationSegment]:
        """
        Run speaker diarization on a mono float32 audio array (synchronous).
        Call from a thread executor when invoked from async code.

        Returns [] if the model is not loaded or pyannote is unavailable.
        """
        if self._pipeline is None:
            logger.warning(
                "DiarizationService.diarize() called but pipeline is not loaded. "
                "Call load_model() first."
            )
            return []

        try:
            import torch  # type: ignore[import]

            waveform = torch.from_numpy(audio).unsqueeze(0)  # shape: (1, samples)
            input_dict = {"waveform": waveform, "sample_rate": sample_rate}

            kwargs: dict = {}
            if num_speakers is not None:
                kwargs["num_speakers"] = num_speakers

            diarization = self._pipeline(input_dict, **kwargs)

            segments: list[DiarizationSegment] = []
            for turn, _, speaker in diarization.itertracks(yield_label=True):
                segments.append(
                    DiarizationSegment(
                        start_ms=int(turn.start * 1000),
                        end_ms=int(turn.end * 1000),
                        speaker_id=speaker,
                    )
                )
            return segments

        except ImportError as exc:
            logger.warning(f"pyannote.audio or torch not installed: {exc}")
            return []
        except Exception as exc:
            logger.error(f"Diarization error: {exc}", exc_info=True)
            return []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_pipeline_local(self):
        """
        Blocking: load pyannote pipeline from local HF cache only.
        Raises if pyannote.audio is not installed.
        """
        try:
            from pyannote.audio import Pipeline  # type: ignore[import]
        except ImportError:
            logger.error(
                "pyannote.audio is not installed. "
                "Run: pip install pyannote.audio torch torchaudio"
            )
            return None

        try:
            pipeline = Pipeline.from_pretrained(
                _PIPELINE_REPO,
                use_auth_token=None,          # no token needed for local load
                cache_dir=self._model_dir,
                local_files_only=True,        # ← never touch the internet
            )
            return pipeline
        except Exception as exc:
            logger.error(f"Local pipeline load failed: {exc}", exc_info=True)
            return None


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_diarization_service: Optional[DiarizationService] = None


def get_diarization_service() -> DiarizationService:
    """Return the process-wide DiarizationService singleton (lazy init)."""
    global _diarization_service
    if _diarization_service is None:
        _diarization_service = DiarizationService()
    return _diarization_service

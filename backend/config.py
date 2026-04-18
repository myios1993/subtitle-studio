"""
Application configuration via Pydantic Settings.
All paths are resolved relative to the project root (subtitle-studio/).
"""

import os
import site
import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Directory layout:
#
#   Source mode (python dev.py):
#     PROJECT_ROOT = subtitle-studio/
#       data/subtitle_studio.db
#       models/whisper/...
#       models/pyannote/...
#
#   PyInstaller bundle (SubtitleStudio.exe):
#     EXE_DIR = dist/SubtitleStudio/        ← models live here (large, user-downloaded)
#     USER_DATA_DIR = %APPDATA%/SubtitleStudio/  ← database lives here (writable, small)
#     _internal/frontend/dist/              ← bundled frontend (read-only)

if getattr(sys, "frozen", False):
    _EXE_DIR = Path(sys.executable).resolve().parent
    # User-writable data goes in %APPDATA%/SubtitleStudio/
    _APPDATA = Path(os.environ.get("APPDATA", _EXE_DIR)) / "SubtitleStudio"
    _APPDATA.mkdir(parents=True, exist_ok=True)
    (_APPDATA / "data").mkdir(exist_ok=True)
    PROJECT_ROOT = _EXE_DIR          # models sit next to the exe
    _DATA_DIR = _APPDATA / "data"    # database sits in AppData
else:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    _DATA_DIR = PROJECT_ROOT / "data"


def _register_nvidia_dll_dirs() -> None:
    """
    Register NVIDIA CUDA runtime DLL directories so that ctranslate2 can find
    cublas64_12.dll, cudnn64_9.dll, etc. on Windows.

    These DLLs are installed via pip packages (nvidia-cublas-cu12, nvidia-cudnn-cu12)
    but their bin/ directories are not on PATH by default.
    """
    if sys.platform != "win32":
        return

    nvidia_subpackages = ["cublas", "cudnn", "cuda_runtime", "cufft", "curand"]
    for sp_dir in site.getsitepackages():
        for pkg in nvidia_subpackages:
            bin_dir = os.path.join(sp_dir, "nvidia", pkg, "bin")
            if os.path.isdir(bin_dir):
                try:
                    os.add_dll_directory(bin_dir)
                except OSError:
                    pass
                # Also add to PATH for subprocess compatibility
                if bin_dir not in os.environ.get("PATH", ""):
                    os.environ["PATH"] = bin_dir + ";" + os.environ.get("PATH", "")


# Register DLL dirs at module import time (before any CUDA library is loaded)
_register_nvidia_dll_dirs()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        env_prefix="SS_",
        extra="ignore",
    )

    # --- Server ---
    host: str = "127.0.0.1"
    port: int = 8765
    debug: bool = False

    # --- Database ---
    database_url: str = f"sqlite+aiosqlite:///{_DATA_DIR / 'subtitle_studio.db'}"

    # --- Model Paths ---
    whisper_model_dir: str = str(PROJECT_ROOT / "models" / "whisper")
    pyannote_model_dir: str = str(PROJECT_ROOT / "models" / "pyannote")
    whisper_model_size: str = "medium"  # tiny | base | small | medium | large-v3 | distil-large-v3

    # --- Audio ---
    audio_sample_rate: int = 16000
    audio_chunk_duration_s: int = 30
    audio_overlap_duration_s: int = 2

    # --- Translation ---
    default_translation_provider: str = "argos"  # argos | openai | deepl | google | compatible
    default_target_language: str = "zh"

    # --- ffmpeg ---
    ffmpeg_path: str = "ffmpeg"  # system PATH or absolute path


settings = Settings()

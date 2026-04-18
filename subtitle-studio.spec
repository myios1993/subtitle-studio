# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for SubtitleStudio
#
# Build:
#   pyinstaller subtitle-studio.spec
#
# Output: dist/SubtitleStudio/SubtitleStudio.exe  (one-folder mode)
#
# Notes:
#   - Run "npm run build" in frontend/ first so dist/ is populated
#   - Model files (Whisper/pyannote) are NOT bundled — downloaded at first run
#   - ffmpeg.exe must be in PATH or placed next to the executable
#   - torch/pyannote are excluded from the bundle (optional GPU feature);
#     users who need diarization should run `python app.py` from source instead

import importlib
import os as _os
import re
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH)


def _pkg_available(name: str) -> bool:
    """Return True if the top-level package is importable at build time."""
    try:
        importlib.import_module(name)
        return True
    except ImportError:
        return False


# ── Packages to EXCLUDE from binary scanning (too large / not needed) ──────
#
# These packages are installed globally on this machine but not used by
# SubtitleStudio.  Excluding them prevents PyInstaller from scanning their
# DLLs during "Looking for dynamic libraries", which is the main cause of
# the very slow build on machines with large ML environments.
#
#   nvidia   — CUDA runtime DLLs (~1.9 GB), only needed with GPU torch
#   torch    — not needed; ctranslate2 handles inference without torch
#   PySide6  — ~380 MB Qt framework; not used by this app
#   PyQt5    — ~100 MB Qt framework; not used by this app
#   flet_desktop — Flutter-based UI toolkit; not used
#   av       — PyAV video/audio; not used (we shell out to ffmpeg instead)
#   triton   — GPU compiler; not needed on Windows

_HEAVY_EXCLUDES = [
    # GUI toolkits installed on this machine but unused
    "PySide6", "PySide2",
    "PyQt5", "PyQt6",
    "flet", "flet_desktop",
    "wx",
    # GPU stack (ctranslate2 can run CPU-only)
    "torch", "torchaudio", "torchvision",
    "nvidia",
    "triton",
    "jax", "flax",
    # AV / media (we use ffmpeg subprocess instead)
    "av",
    # Misc heavy packages unused by this project
    "matplotlib",
    # tkinter is intentionally NOT excluded — used for error dialogs in app.py
    "IPython",
    "jupyter",
    "notebook",
    "pandas",
    "sklearn",
    "cv2",
    "tensorflow",
    "keras",
]

# ── Data files ─────────────────────────────────────────────────────────────

datas = [
    (str(ROOT / "frontend" / "dist"), "frontend/dist"),
    (str(ROOT / "data"), "data"),
]

if _pkg_available("faster_whisper"):
    datas += collect_data_files("faster_whisper")

# pywebview: platforms/ subpackage is NOT collected by the built-in hook,
# but it's needed at runtime for the Windows WebView2 backend.
if _pkg_available("webview"):
    datas += collect_data_files("webview")   # covers js/, lib/, platforms/

# pyannote/speechbrain excluded from bundle (needs torch — too large)

# ── Hidden imports ─────────────────────────────────────────────────────────

hiddenimports = [
    # FastAPI / Starlette
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "starlette.routing",
    "starlette.staticfiles",
    # SQLAlchemy / DB
    "sqlalchemy.dialects.sqlite",
    "aiosqlite",
    # Pydantic
    "pydantic.deprecated.decorator",
]

# Add optional packages only if installed AND not in the exclude list
_optional = [
    ("webview", [
        "webview",
        "webview.platforms.winforms",   # Windows WebView2 backend
        "webview.platforms.edgechromium",
        "webview.platforms.mshtml",
        "clr_loader",                   # pythonnet CLR loader used by winforms
        "clr",
    ]),
    ("sounddevice", ["sounddevice"]),
    ("scipy", ["scipy.signal"]),
    ("ctranslate2", ["ctranslate2"]),
    ("openai", ["openai"]),
    ("huggingface_hub", ["huggingface_hub"]),
]
for _top, _mods in _optional:
    if _pkg_available(_top) and _top not in _HEAVY_EXCLUDES:
        for _m in _mods:
            try:
                importlib.import_module(_m)
                hiddenimports.append(_m)
            except ImportError:
                pass  # silently skip unavailable sub-modules

# backend package — MUST be explicit because uvicorn.run() receives it as a
# string ("backend.main:app") which PyInstaller cannot trace statically.
hiddenimports += collect_submodules("backend")

# ── Analysis ───────────────────────────────────────────────────────────────

a = Analysis(
    [str(ROOT / "app.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(ROOT / "pyinstaller_hooks")],  # custom hooks (see below)
    hooksconfig={},
    runtime_hooks=[],
    excludes=_HEAVY_EXCLUDES,
    noarchive=False,
)

# ── Post-analysis binary filter ────────────────────────────────────────────
#
# Even with excludes, PyInstaller may pull in DLLs transitively.
# Strip anything from the heavy packages so the final bundle stays small.

_BINARY_EXCLUDE_PATTERNS = [
    r"[\\/]nvidia[\\/]",
    r"[\\/]torch[\\/]",
    r"[\\/]PySide[26][\\/]",
    r"[\\/]PyQt[56][\\/]",
    r"[\\/]flet",
    r"cublas",
    r"cudnn",
    r"cufft",
    r"curand",
    r"nvcuda",
    r"nvrtc",
    r"rocm",
    r"hip[a-z]",
]
_exclude_re = re.compile("|".join(_BINARY_EXCLUDE_PATTERNS), re.IGNORECASE)

_before = len(a.binaries)
a.binaries = [b for b in a.binaries if not _exclude_re.search(b[1])]
print(f"[spec] Binary filter: {_before} -> {len(a.binaries)} entries (removed {_before - len(a.binaries)})")

# ── Package ────────────────────────────────────────────────────────────────

pyz = PYZ(a.pure)

_icon_path = str(ROOT / "assets" / "app.ico")
_icon = _icon_path if _os.path.isfile(_icon_path) else None

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SubtitleStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # 正式发布：不显示控制台黑框
    disable_windowed_traceback=False,
    icon=_icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SubtitleStudio",
)

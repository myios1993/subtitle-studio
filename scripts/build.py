"""
Build script for SubtitleStudio desktop application.

Steps:
  1. npm run build   — compile Vue frontend to frontend/dist/
  2. pyinstaller     — package Python backend + frontend into dist/SubtitleStudio/

Usage:
    python scripts/build.py
    python scripts/build.py --skip-frontend   # skip npm build (use existing dist/)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print(f"\n>>> {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode != 0:
        print(f"Command failed with code {result.returncode}")
        sys.exit(result.returncode)


def build_frontend() -> None:
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    run([npm, "run", "build"], cwd=ROOT / "frontend")
    dist = ROOT / "frontend" / "dist"
    if not dist.is_dir():
        print("Frontend dist/ not found after build — aborting.")
        sys.exit(1)
    print(f"Frontend built → {dist}")


def build_exe() -> None:
    spec = ROOT / "subtitle-studio.spec"
    run([sys.executable, "-m", "PyInstaller", str(spec), "--noconfirm"], cwd=ROOT)
    output = ROOT / "dist" / "SubtitleStudio" / "SubtitleStudio.exe"
    if output.exists():
        print(f"\nBuild complete: {output}")
    else:
        print("\nBuild finished but SubtitleStudio.exe not found — check PyInstaller output.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build SubtitleStudio")
    parser.add_argument("--skip-frontend", action="store_true", help="Skip npm build")
    args = parser.parse_args()

    if not args.skip_frontend:
        print("=== Step 1: Build Vue frontend ===")
        build_frontend()
    else:
        print("=== Step 1: Skipping frontend build ===")

    print("\n=== Step 2: Package with PyInstaller ===")
    build_exe()


if __name__ == "__main__":
    main()

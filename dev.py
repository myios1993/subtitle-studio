"""
SubtitleStudio development launcher.

Usage:
    python dev.py          # backend only (run `npm run dev` in frontend/ separately)
    python dev.py --all    # backend + frontend Vite dev server (requires Node in PATH)
    python dev.py --prod   # backend serving the built frontend (dist/)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import threading
from pathlib import Path

project_root = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))


def run_backend(reload: bool = True) -> None:
    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host="127.0.0.1",
        port=8765,
        reload=reload,
        reload_dirs=[str(project_root / "backend")] if reload else [],
    )


def run_frontend_dev() -> None:
    frontend_dir = project_root / "frontend"
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    proc = subprocess.Popen(
        [npm, "run", "dev"],
        cwd=frontend_dir,
    )
    proc.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SubtitleStudio dev launcher")
    parser.add_argument("--all", action="store_true", help="Also start Vite frontend dev server")
    parser.add_argument("--prod", action="store_true", help="Serve built frontend from dist/ (no reload)")
    args = parser.parse_args()

    if args.all:
        # Start Vite in a thread, backend in main thread
        t = threading.Thread(target=run_frontend_dev, daemon=True)
        t.start()
        print("  Frontend: http://localhost:5173  (Vite dev server)")
        print("  Backend:  http://localhost:8765")
        run_backend(reload=True)
    elif args.prod:
        # Serve pre-built dist/ via FastAPI static files
        # Mount is registered in main.py when frontend/dist exists
        print("  Starting in production mode — http://localhost:8765")
        run_backend(reload=False)
    else:
        print("  Backend:  http://localhost:8765")
        print("  Frontend: run `npm run dev` in frontend/ (http://localhost:5173)")
        run_backend(reload=True)

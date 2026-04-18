"""
SubtitleStudio — FastAPI application entry point.
"""

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.config import settings
from backend.database import init_db
from backend.api import projects, segments, export, pipeline, audio, settings as settings_api, models as models_api, setup as setup_api
from backend.websocket.hub import ws_manager

# Path to the compiled Vue frontend.
# In a PyInstaller bundle the dist/ folder is inside _MEIPASS (= _internal/),
# not next to the exe, so we look there first.
if getattr(sys, "frozen", False):
    _FRONTEND_DIST = Path(sys._MEIPASS) / "frontend" / "dist"  # type: ignore[attr-defined]
else:
    _FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB tables, reset stale statuses. Shutdown: cleanup."""
    logger.info("SubtitleStudio starting up...")
    await init_db()
    logger.info("Database initialized.")

    # Reset any projects stuck in 'processing'/'capturing' from a previous crash
    from backend.database import async_session_factory
    from backend.models.project import Project
    from sqlalchemy import update, or_
    async with async_session_factory() as session:
        await session.execute(
            update(Project)
            .where(or_(Project.status == "processing", Project.status == "capturing"))
            .values(status="error")
        )
        await session.commit()
    logger.info("Stale pipeline statuses reset.")

    yield
    logger.info("SubtitleStudio shutting down.")


app = FastAPI(
    title="SubtitleStudio",
    version="0.1.0",
    description="Automatic subtitle generation with speaker diarization and translation",
    lifespan=lifespan,
)

# CORS — allow the Vue frontend dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- REST API routers ---
app.include_router(projects.router)
app.include_router(segments.router)
app.include_router(export.router)
app.include_router(pipeline.router)
app.include_router(audio.router)
app.include_router(settings_api.router)
app.include_router(models_api.router)
app.include_router(setup_api.router)


# --- Serve compiled Vue frontend (production / pywebview mode) ---
# Mount only when the dist/ directory exists so that dev mode (Vite proxy) is
# not affected.  API routes are registered first so they take precedence.
if _FRONTEND_DIST.is_dir():
    # Serve all static assets under /assets, /icons.svg, etc.
    app.mount("/assets", StaticFiles(directory=str(_FRONTEND_DIST / "assets")), name="assets")

    # Serve other static files at root level (favicon.svg, icons.svg …)
    for _static_file in _FRONTEND_DIST.iterdir():
        if _static_file.is_file() and _static_file.name != "index.html":
            pass  # handled by catch-all below

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        """Catch-all: serve index.html for Vue Router history-mode SPA."""
        # Try to serve an exact file first (e.g., favicon.svg)
        candidate = _FRONTEND_DIST / full_path
        if candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(_FRONTEND_DIST / "index.html"))


# --- WebSocket endpoint ---
@app.websocket("/ws/{project_id}")
async def websocket_endpoint(websocket: WebSocket, project_id: int):
    """WebSocket endpoint for real-time pipeline events."""
    await ws_manager.connect(websocket, project_id)
    try:
        while True:
            # Keep connection alive; client may send ping/control messages
            data = await websocket.receive_text()
            # Echo or handle client messages if needed
            logger.debug(f"WS message from project {project_id}: {data}")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, project_id)


# --- Health check ---
@app.get("/health")
async def health_check():
    return {"status": "ok", "version": "0.1.0"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )

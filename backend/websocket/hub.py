"""
WebSocket connection manager and broadcast hub.
Manages active WebSocket connections and broadcasts pipeline events
(segment_created, segment_updated, pipeline_status, pipeline_done)
to all connected clients.
"""

import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections grouped by project_id."""

    def __init__(self):
        # project_id -> set of active WebSocket connections
        self._connections: dict[int, set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, project_id: int) -> None:
        """Accept a new WebSocket connection and register it for a project."""
        await websocket.accept()
        if project_id not in self._connections:
            self._connections[project_id] = set()
        self._connections[project_id].add(websocket)
        logger.info(f"WebSocket connected for project {project_id}. "
                     f"Total connections: {len(self._connections[project_id])}")

    def disconnect(self, websocket: WebSocket, project_id: int) -> None:
        """Remove a WebSocket connection."""
        if project_id in self._connections:
            self._connections[project_id].discard(websocket)
            if not self._connections[project_id]:
                del self._connections[project_id]
        logger.info(f"WebSocket disconnected for project {project_id}")

    async def broadcast(self, project_id: int, event: str, data: Any = None) -> None:
        """Broadcast a JSON event to all connections for a project."""
        if project_id not in self._connections:
            return

        message = json.dumps({"event": event, "data": data}, ensure_ascii=False, default=str)
        dead_connections = []

        for ws in self._connections[project_id]:
            try:
                await ws.send_text(message)
            except Exception:
                dead_connections.append(ws)

        # Clean up dead connections
        for ws in dead_connections:
            self._connections[project_id].discard(ws)

    async def send_personal(self, websocket: WebSocket, event: str, data: Any = None) -> None:
        """Send a message to a specific connection."""
        message = json.dumps({"event": event, "data": data}, ensure_ascii=False, default=str)
        await websocket.send_text(message)

    @property
    def active_project_ids(self) -> list[int]:
        """Return list of project IDs with active connections."""
        return list(self._connections.keys())


# Singleton instance used across the application
ws_manager = ConnectionManager()

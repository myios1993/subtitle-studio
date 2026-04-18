"""
Project ORM model.
Each project represents one subtitle generation session (a file or a live capture).
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # "loopback" | "microphone" | "file"
    capture_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="file")

    # "idle" | "capturing" | "processing" | "done" | "error"
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="idle")

    # File paths (only for file mode)
    source_audio_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    source_video_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    playback_video_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)

    # ASR detected main language of the source
    source_language: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    speakers: Mapped[list["Speaker"]] = relationship(  # type: ignore[name-defined]
        "Speaker", back_populates="project", cascade="all, delete-orphan", lazy="selectin"
    )
    segments: Mapped[list["SubtitleSegment"]] = relationship(  # type: ignore[name-defined]
        "SubtitleSegment", back_populates="project", cascade="all, delete-orphan",
        order_by="SubtitleSegment.start_ms", lazy="selectin"
    )

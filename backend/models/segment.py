"""
SubtitleSegment ORM model.
Each row is one subtitle entry with timestamps in integer milliseconds.
"""

from typing import Optional

from sqlalchemy import String, Integer, Float, Boolean, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class SubtitleSegment(Base):
    __tablename__ = "subtitle_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # 1-based display order; recomputed on export
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Timestamps in integer milliseconds — avoids floating-point drift
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    # ASR original text
    original_text: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Chinese translation (null = not yet translated, or source is already Chinese)
    translated_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Detected language of this segment (e.g. "en", "zh", "ja")
    original_language: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)

    # Speaker reference — stores the raw speaker_id string (e.g. "SPEAKER_00")
    # Links to Speaker table via (project_id, speaker_id) but kept as plain string
    # to allow segments to be created before diarization completes (speaker_id=null)
    speaker_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Whether the user has manually edited this segment
    is_manually_edited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ASR confidence score 0.0 ~ 1.0
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="segments")  # type: ignore[name-defined]

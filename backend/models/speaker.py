"""
Speaker ORM model.
Normalized speaker info — renaming a speaker only requires updating one row.
"""

from typing import Optional

from sqlalchemy import String, Integer, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class Speaker(Base):
    __tablename__ = "speakers"
    __table_args__ = (
        UniqueConstraint("project_id", "speaker_id", name="uq_project_speaker"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Raw speaker ID from pyannote, e.g. "SPEAKER_00"
    speaker_id: Mapped[str] = mapped_column(String(50), nullable=False)

    # User-assigned label, e.g. "Alice" or "Zhang San"
    label: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Display color in the frontend, e.g. "#4A90D9"
    color: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)

    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="speakers")  # type: ignore[name-defined]

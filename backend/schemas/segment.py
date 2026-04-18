"""
Pydantic schemas for SubtitleSegment API requests and responses.
"""

from typing import Optional

from pydantic import BaseModel, Field


class SegmentCreate(BaseModel):
    start_ms: int = Field(..., ge=0)
    end_ms: int = Field(..., ge=0)
    original_text: str = ""
    translated_text: Optional[str] = None
    original_language: Optional[str] = None
    speaker_id: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class SegmentUpdate(BaseModel):
    start_ms: Optional[int] = Field(default=None, ge=0)
    end_ms: Optional[int] = Field(default=None, ge=0)
    original_text: Optional[str] = None
    translated_text: Optional[str] = None
    speaker_id: Optional[str] = None
    is_manually_edited: Optional[bool] = None


class SegmentRead(BaseModel):
    id: int
    project_id: int
    sequence: int
    start_ms: int
    end_ms: int
    original_text: str
    translated_text: Optional[str] = None
    original_language: Optional[str] = None
    speaker_id: Optional[str] = None
    is_manually_edited: bool = False
    confidence: Optional[float] = None

    model_config = {"from_attributes": True}


class SegmentBatchSpeakerUpdate(BaseModel):
    """Batch update speaker_id for multiple segments (used by diarization backfill)."""
    updates: list[dict] = Field(
        ...,
        description="List of {segment_id: int, speaker_id: str}",
    )

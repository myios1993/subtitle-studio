"""
Pydantic schemas for Project API requests and responses.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ---------- Speaker ----------

class SpeakerBase(BaseModel):
    speaker_id: str
    label: Optional[str] = None
    color: Optional[str] = None


class SpeakerRead(SpeakerBase):
    id: int
    project_id: int

    model_config = {"from_attributes": True}


class SpeakerUpdate(BaseModel):
    label: Optional[str] = None
    color: Optional[str] = None


# ---------- Project ----------

class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    capture_mode: str = Field(default="file", pattern=r"^(loopback|microphone|file)$")
    source_audio_path: Optional[str] = None
    source_video_path: Optional[str] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    status: Optional[str] = Field(default=None, pattern=r"^(idle|capturing|processing|done|error)$")
    source_language: Optional[str] = None


class ProjectRead(BaseModel):
    id: int
    name: str
    capture_mode: str
    status: str
    source_audio_path: Optional[str] = None
    source_video_path: Optional[str] = None
    playback_video_path: Optional[str] = None
    source_language: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    speakers: list[SpeakerRead] = []
    segment_count: int = 0

    model_config = {"from_attributes": True}


class ProjectListRead(BaseModel):
    """Lightweight listing without segments."""
    id: int
    name: str
    capture_mode: str
    status: str
    source_language: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    segment_count: int = 0

    model_config = {"from_attributes": True}

"""
ORM models package.
Import all models here so that Base.metadata is aware of them.
"""

from backend.models.project import Project
from backend.models.speaker import Speaker
from backend.models.segment import SubtitleSegment
from backend.models.settings import AppSettings

__all__ = ["Project", "Speaker", "SubtitleSegment", "AppSettings"]

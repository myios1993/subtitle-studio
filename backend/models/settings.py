"""
AppSettings ORM model.
Key-value store for encrypted API keys and app preferences.
Values are encrypted via Windows DPAPI before storage.
"""

from sqlalchemy import String, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class AppSettings(Base):
    __tablename__ = "app_settings"
    __table_args__ = (
        UniqueConstraint("key", name="uq_settings_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Setting key, e.g. "hf_token", "openai_api_key", "translation_provider"
    key: Mapped[str] = mapped_column(String(100), nullable=False)

    # Encrypted value (DPAPI encrypted, base64 encoded)
    # For non-sensitive values, stored as plaintext
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Whether this value is encrypted
    is_encrypted: Mapped[bool] = mapped_column(default=False)

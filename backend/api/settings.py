"""
FastAPI router for application settings management.

Exposes /api/settings for CRUD operations on key-value app settings stored
in the AppSettings table (AppSettings ORM: backend/models/settings.py).

Sensitive values (keys containing "key", "token", or "secret") are masked
as "***" in GET responses to prevent accidental credential exposure in logs
and client-side storage.

TODO: Implement value encryption using Windows DPAPI (or libsecret on Linux)
      before writing to the DB, and decryption on read, so that credentials
      are not stored as plaintext. The AppSettings.is_encrypted field and the
      intent are already modelled in the schema.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_upsert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.settings import AppSettings
from backend.services.translation import TranslationService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Keys whose values must be masked in GET responses
_SENSITIVE_SUBSTRINGS = ("key", "token", "secret")

# Settings keys used to store translation configuration
_TRANSLATION_KEYS = (
    "translation_provider",
    "translation_api_key",
    "translation_base_url",
    "translation_model",
    "translation_target_lang",
)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class SettingValueBody(BaseModel):
    """Request body for PUT /api/settings/{key}."""

    value: str


class TranslationConfigBody(BaseModel):
    """Request body for PUT /api/settings/translation/config."""

    provider: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    target_lang: Optional[str] = None


class TranslationConfigRead(BaseModel):
    """Response schema for GET /api/settings/translation/config."""

    provider: str
    api_key: str       # masked if sensitive
    base_url: str
    model: str
    target_lang: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_sensitive(key: str) -> bool:
    """Return True if *key* contains any sensitive substring (case-insensitive)."""
    lower = key.lower()
    return any(sub in lower for sub in _SENSITIVE_SUBSTRINGS)


def _mask(key: str, value: str) -> str:
    """Return masked value for sensitive keys, raw value otherwise."""
    return "***" if _is_sensitive(key) else value


async def _get_setting(
    key: str,
    session: AsyncSession,
) -> Optional[AppSettings]:
    """Fetch a single AppSettings row by key, or None if absent."""
    stmt = select(AppSettings).where(AppSettings.key == key)
    return (await session.execute(stmt)).scalar_one_or_none()


async def _upsert_setting(
    key: str,
    value: str,
    session: AsyncSession,
) -> None:
    """
    Insert or update an AppSettings row.

    TODO: encrypt *value* with DPAPI here when encryption support is added.
          Set is_encrypted=True for keys that match _SENSITIVE_SUBSTRINGS.
    """
    stmt = (
        sqlite_upsert(AppSettings)
        .values(key=key, value=value, is_encrypted=False)
        .on_conflict_do_update(
            index_elements=["key"],
            set_={"value": value, "is_encrypted": False},
        )
    )
    await session.execute(stmt)


# ---------------------------------------------------------------------------
# Routes — general settings
# ---------------------------------------------------------------------------


@router.get("", response_model=dict[str, str], summary="List all settings (sensitive values masked)")
async def list_settings(
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """
    Return all settings as a ``{key: value}`` dict.

    Values for keys whose name contains "key", "token", or "secret" are
    replaced with ``"***"`` to prevent credential leakage.
    """
    stmt = select(AppSettings).order_by(AppSettings.key)
    rows: list[AppSettings] = list((await db.execute(stmt)).scalars().all())
    return {row.key: _mask(row.key, row.value) for row in rows}


@router.get(
    "/translation/config",
    response_model=TranslationConfigRead,
    summary="Get structured translation configuration",
)
async def get_translation_config(
    db: AsyncSession = Depends(get_db),
) -> TranslationConfigRead:
    """
    Return the current translation configuration as a structured object.

    The ``api_key`` field is always masked in the response.
    """
    stmt = select(AppSettings).where(AppSettings.key.in_(_TRANSLATION_KEYS))
    rows = (await db.execute(stmt)).scalars().all()
    raw: dict[str, str] = {row.key: row.value for row in rows}

    return TranslationConfigRead(
        provider=raw.get("translation_provider", "argos"),
        api_key="***" if raw.get("translation_api_key") else "",
        base_url=raw.get("translation_base_url", ""),
        model=raw.get("translation_model", "gpt-4o-mini"),
        target_lang=raw.get("translation_target_lang", "zh"),
    )


@router.post(
    "/translation/test",
    summary="Test the current translation configuration",
)
async def test_translation_config() -> dict:
    """
    Translate a short test sentence with the currently saved configuration.

    Returns ``{"ok": bool, "provider": str, "result": str, "error": str}``.
    Does **not** require an active DB session because the TranslationService
    loads settings via its own session factory.
    """
    return await TranslationService.test_translation("Hello, world.")


@router.put(
    "/translation/config",
    summary="Update translation configuration",
)
async def put_translation_config(
    body: TranslationConfigBody,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Persist each provided translation config field as an individual settings key.

    Only non-None fields in the request body are written, so callers can do
    partial updates (e.g. update only ``model`` without sending ``api_key``).
    Returns the list of keys that were updated.
    """
    mapping: dict[str, Optional[str]] = {
        "translation_provider": body.provider,
        "translation_api_key": body.api_key,
        "translation_base_url": body.base_url,
        "translation_model": body.model,
        "translation_target_lang": body.target_lang,
    }

    updated_keys: list[str] = []
    for key, value in mapping.items():
        if value is not None:
            await _upsert_setting(key, value, db)
            updated_keys.append(key)

    # Invalidate the in-process translation config cache so the next
    # translation job picks up the new settings immediately.
    TranslationService.invalidate_config_cache()

    return {"updated_keys": updated_keys, "updated": True}


@router.get(
    "/{key}",
    response_model=dict[str, str],
    summary="Get a single setting value",
)
async def get_setting(
    key: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """
    Return the value for a single settings key.

    Raises 404 if the key does not exist.  Sensitive values are masked.
    """
    row = await _get_setting(key, db)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Setting '{key}' not found.",
        )
    return {"key": row.key, "value": _mask(row.key, row.value)}


@router.put("/{key}", summary="Create or update a setting")
async def put_setting(
    key: str,
    body: SettingValueBody,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Upsert a setting value.

    If the key is part of the translation configuration, the in-process
    config cache is invalidated so the new value takes effect immediately.

    Returns ``{"key": <key>, "updated": true}``.
    """
    await _upsert_setting(key, body.value, db)

    if key in _TRANSLATION_KEYS:
        TranslationService.invalidate_config_cache()

    return {"key": key, "updated": True}


@router.delete("/{key}", summary="Delete a setting")
async def delete_setting(
    key: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Delete a setting by key.

    Raises 404 if the key does not exist.
    Returns ``{"key": <key>, "deleted": true}``.
    """
    stmt = delete(AppSettings).where(AppSettings.key == key)
    result = await db.execute(stmt)

    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Setting '{key}' not found.",
        )

    if key in _TRANSLATION_KEYS:
        TranslationService.invalidate_config_cache()

    return {"key": key, "deleted": True}

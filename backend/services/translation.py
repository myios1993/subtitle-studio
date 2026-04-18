"""
Multi-provider translation service using the Strategy pattern.

Supported providers:
  - argos      : Offline translation via argostranslate (no API key needed)
  - openai     : OpenAI ChatCompletion (GPT-4o-mini by default)
  - deepl      : DeepL REST API
  - compatible : Any OpenAI-compatible endpoint (Azure, DeepSeek, Ollama, etc.)

Usage:
    config = {"provider": "openai", "api_key": "sk-...", "target_lang": "zh"}
    translator = get_translator(config)
    translations = await translator.translate_batch(["Hello", "World"])
"""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from functools import lru_cache
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import async_session_factory
from backend.models.segment import SubtitleSegment
from backend.models.settings import AppSettings
from backend.websocket.hub import ws_manager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

BATCH_SIZE = 20


class TranslatorBase(ABC):
    """Abstract base class for all translation backends."""

    @abstractmethod
    async def translate_batch(
        self,
        texts: list[str],
        source_lang: str = "auto",
    ) -> list[str]:
        """
        Translate a list of text strings.

        Args:
            texts: Source strings to translate.
            source_lang: BCP-47 language code of the source, or "auto" to detect.

        Returns:
            List of translated strings in the same order as *texts*.
        """


# ---------------------------------------------------------------------------
# Argos (offline)
# ---------------------------------------------------------------------------


class ArgosTranslator(TranslatorBase):
    """
    Offline translation using argostranslate.

    argostranslate's translate() call is synchronous; we run it in the
    default thread-pool executor to keep the event loop free.
    """

    def __init__(self, target_lang: str = "zh") -> None:
        self.target_lang = target_lang
        # Package availability is checked (and auto-installed) lazily in
        # translate_batch() via _ensure_package(), so construction never fails.

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_package(self, from_code: str, to_code: str) -> None:
        """Auto-install the required argostranslate language package if missing."""
        try:
            from argostranslate import package, translate
        except ImportError as exc:
            raise ImportError(
                "argostranslate is not installed. Run: pip install argostranslate"
            ) from exc

        # Check if the pair is already installed
        installed = translate.get_installed_languages()
        installed_codes = {lang.code for lang in installed}
        if from_code in installed_codes and to_code in installed_codes:
            # Further check: verify translation path exists
            from_lang = next((l for l in installed if l.code == from_code), None)
            if from_lang:
                to_lang_obj = from_lang.get_translation(
                    next((l for l in installed if l.code == to_code), None)
                )
                if to_lang_obj is not None:
                    return  # already installed and path exists

        # Need to install
        logger.info(f"Installing argostranslate package: {from_code} → {to_code}")
        package.update_package_index()
        available = package.get_available_packages()
        pkg = next(
            (p for p in available if p.from_code == from_code and p.to_code == to_code),
            None,
        )
        if pkg is None:
            raise RuntimeError(
                f"No argostranslate package found for {from_code}→{to_code}. "
                f"Available pairs may not include this language combination."
            )
        package.install_from_path(pkg.download())
        logger.info(f"argostranslate package installed: {from_code}→{to_code}")

    @staticmethod
    def _sync_translate(text: str, from_code: str, to_code: str) -> str:
        """Blocking call — must be run in an executor."""
        from argostranslate import translate  # lazy import

        return translate.translate(text, from_code, to_code)

    def _resolve_lang_code(self, source_lang: str) -> str:
        """Normalise lang codes: 'zh-CN' → 'zh', 'auto' → 'en' (fallback)."""
        if source_lang in ("auto", ""):
            return "en"
        # Strip region suffix
        return source_lang.split("-")[0].lower()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def translate_batch(
        self,
        texts: list[str],
        source_lang: str = "auto",
    ) -> list[str]:
        from_code = self._resolve_lang_code(source_lang)
        to_code = self.target_lang.lower()

        if from_code == to_code:
            raise RuntimeError(
                f"ArgosTranslate: 源语言与目标语言相同 ({from_code})。"
                "请在设置中将目标语言修改为与源语言不同的语言，"
                "或将翻译引擎切换为支持同语言处理的 LLM（如 OpenAI / 兼容协议）。"
            )

        # Ensure the required language package is installed (auto-installs if not)
        self._ensure_package(from_code, to_code)

        loop = asyncio.get_running_loop()
        tasks = [
            loop.run_in_executor(None, self._sync_translate, text, from_code, to_code)
            for text in texts
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        translated: list[str] = []
        for original, result in zip(texts, results):
            if isinstance(result, Exception):
                logger.error(
                    "ArgosTranslator failed for text=%r: %s", original[:40], result
                )
                translated.append(original)  # fall back to source
            else:
                translated.append(result)  # type: ignore[arg-type]

        return translated


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------


class OpenAITranslator(TranslatorBase):
    """
    Translation via OpenAI ChatCompletion API.

    Sends an entire batch in a single request using a structured JSON prompt
    to minimise latency and token overhead.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: Optional[str] = None,
        target_lang: str = "zh",
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.target_lang = target_lang

        # Rolling context window: last 3 translated lines for consistency
        self._context_lines: list[str] = []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_client(self):
        """Build (and return) an AsyncOpenAI client. Import lazily."""
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise ImportError(
                "openai package is not installed. Install with: pip install openai"
            ) from exc

        kwargs: dict = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        return AsyncOpenAI(**kwargs)

    def _system_prompt(self) -> str:
        base = (
            f"You are a professional subtitle translator. "
            f"Translate the following subtitles to {self.target_lang}. "
            "Return ONLY a JSON array of translated strings in the same order, "
            "no explanation."
        )
        if self._context_lines:
            ctx = "\n".join(self._context_lines)
            base += f"\n\nRecent context for consistency:\n{ctx}"
        return base

    async def _call_api(self, texts: list[str]) -> list[str]:
        client = self._build_client()
        user_content = json.dumps(texts, ensure_ascii=False)
        response = await client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": user_content},
            ],
            temperature=0.1,
        )
        raw = response.choices[0].message.content or "[]"

        # Strip markdown code fences if the model wraps in ```json … ```
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
            raw = raw.rsplit("```", 1)[0]

        parsed: list[str] = json.loads(raw)
        if not isinstance(parsed, list) or len(parsed) != len(texts):
            raise ValueError(
                f"OpenAI returned unexpected JSON shape. "
                f"Expected list of {len(texts)}, got: {raw[:200]}"
            )
        return parsed

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def translate_batch(
        self,
        texts: list[str],
        source_lang: str = "auto",
    ) -> list[str]:
        if not texts:
            return []

        try:
            translated = await self._call_api(texts)
        except Exception as exc:
            logger.error("OpenAITranslator batch failed: %s", exc)
            return texts  # fall back to source

        # Update rolling context (keep last 3)
        self._context_lines = translated[-3:]
        return translated


# ---------------------------------------------------------------------------
# DeepL
# ---------------------------------------------------------------------------


class DeepLTranslator(TranslatorBase):
    """Translation via the DeepL REST API using the deepl Python SDK."""

    def __init__(self, api_key: str, target_lang: str = "ZH") -> None:
        self.api_key = api_key
        # DeepL expects uppercase language codes, e.g. "ZH", "DE", "FR"
        self.target_lang = target_lang.upper()

    def _build_client(self):
        try:
            import deepl
        except ImportError as exc:
            raise ImportError(
                "deepl package is not installed. Install with: pip install deepl"
            ) from exc
        return deepl.Translator(self.api_key)

    async def translate_batch(
        self,
        texts: list[str],
        source_lang: str = "auto",
    ) -> list[str]:
        if not texts:
            return []

        source: Optional[str] = None if source_lang == "auto" else source_lang.upper()

        loop = asyncio.get_running_loop()

        def _sync_call() -> list[str]:
            client = self._build_client()
            results = client.translate_text(
                texts,
                source_lang=source,
                target_lang=self.target_lang,
            )
            # deepl returns TextResult objects; extract .text
            return [r.text for r in results]

        try:
            return await loop.run_in_executor(None, _sync_call)
        except Exception as exc:
            logger.error("DeepLTranslator batch failed: %s", exc)
            return texts


# ---------------------------------------------------------------------------
# Compatible (OpenAI-protocol third-party endpoints)
# ---------------------------------------------------------------------------


class CompatibleAPITranslator(OpenAITranslator):
    """
    Translation via any OpenAI-compatible API endpoint.

    Examples: Azure OpenAI, DeepSeek, Ollama, LM Studio, etc.
    *base_url* is **required** for this translator.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str = "gpt-4o-mini",
        target_lang: str = "zh",
    ) -> None:
        if not base_url:
            raise ValueError(
                "CompatibleAPITranslator requires base_url to be set "
                "(e.g. 'https://<resource>.openai.azure.com/' for Azure)."
            )
        super().__init__(
            api_key=api_key,
            model=model,
            base_url=base_url,
            target_lang=target_lang,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_translator(config: dict) -> TranslatorBase:
    """
    Instantiate the correct translator from a config dict.

    Expected keys:
        provider   : "argos" | "openai" | "deepl" | "compatible"
        api_key    : str (not required for argos)
        base_url   : str (required for compatible)
        model      : str (openai / compatible only)
        target_lang: str

    Raises:
        ValueError: if provider is unknown or required fields are missing.
    """
    provider = config.get("provider", "argos").lower()
    api_key: str = config.get("api_key", "")
    base_url: Optional[str] = config.get("base_url") or None
    model: str = config.get("model", "gpt-4o-mini")
    target_lang: str = config.get("target_lang", "zh")

    if provider == "argos":
        return ArgosTranslator(target_lang=target_lang)

    if provider == "openai":
        if not api_key:
            raise ValueError("OpenAITranslator requires api_key.")
        return OpenAITranslator(
            api_key=api_key, model=model, base_url=base_url, target_lang=target_lang
        )

    if provider == "deepl":
        if not api_key:
            raise ValueError("DeepLTranslator requires api_key.")
        return DeepLTranslator(api_key=api_key, target_lang=target_lang)

    if provider == "compatible":
        if not api_key:
            raise ValueError("CompatibleAPITranslator requires api_key.")
        if not base_url:
            raise ValueError("CompatibleAPITranslator requires base_url.")
        return CompatibleAPITranslator(
            api_key=api_key, base_url=base_url, model=model, target_lang=target_lang
        )

    raise ValueError(
        f"Unknown translation provider: {provider!r}. "
        "Valid options: argos, openai, deepl, compatible."
    )


# ---------------------------------------------------------------------------
# High-level TranslationService
# ---------------------------------------------------------------------------


class TranslationService:
    """
    Orchestrates translation of subtitle segments for a project.

    Config is loaded lazily from the AppSettings table and cached in-process.
    The cache is invalidated whenever *invalidate_config_cache()* is called
    (typically after the user updates settings via the API).
    """

    _config_cache: Optional[dict] = None

    # ------------------------------------------------------------------
    # Config loading
    # ------------------------------------------------------------------

    @classmethod
    def invalidate_config_cache(cls) -> None:
        """Clear the cached translation config (call after settings are updated)."""
        cls._config_cache = None

    @classmethod
    async def _load_config(cls) -> dict:
        """
        Read translation settings from AppSettings DB table.

        Keys: translation_provider, translation_api_key, translation_base_url,
              translation_model, translation_target_lang
        """
        if cls._config_cache is not None:
            return cls._config_cache

        async with async_session_factory() as session:
            stmt = select(AppSettings).where(
                AppSettings.key.in_([
                    "translation_provider",
                    "translation_api_key",
                    "translation_base_url",
                    "translation_model",
                    "translation_target_lang",
                ])
            )
            rows = (await session.execute(stmt)).scalars().all()

        raw = {row.key: row.value for row in rows}
        config = {
            "provider":    raw.get("translation_provider", "argos"),
            "api_key":     raw.get("translation_api_key", ""),
            "base_url":    raw.get("translation_base_url", ""),
            "model":       raw.get("translation_model", "gpt-4o-mini"),
            "target_lang": raw.get("translation_target_lang", "zh"),
        }

        cls._config_cache = config
        return config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def translate_segments(
        self,
        project_id: int,
        segment_ids: Optional[list[int]] = None,
        source_lang_override: Optional[str] = None,
        retranslate: bool = False,
    ) -> None:
        """
        Translate subtitle segments for *project_id*.

        When *segment_ids* is provided, only those segments are translated.
        When *segment_ids* is None, all untranslated segments for the project
        are fetched and translated.

        Args:
            project_id:  Project these segments belong to.
            segment_ids: Ordered list of segment PKs to translate, or None for all
                         untranslated/all segments.
            source_lang_override: If set, use this as source_lang for all batches
                                  instead of per-segment original_language.
            retranslate: If True, re-translate even segments that already have
                         translated_text (useful for switching translators/languages).
        """
        try:
            config = await self._load_config()
            translator = get_translator(config)
        except Exception as exc:
            err_msg = str(exc)
            logger.error(
                "TranslationService: failed to initialise translator: %s", err_msg
            )
            await ws_manager.broadcast(
                project_id,
                "translation_error",
                {"error": f"翻译服务初始化失败: {err_msg}"},
            )
            return

        target_lang = config.get("target_lang", "zh").lower()

        async with async_session_factory() as session:
            if segment_ids is not None:
                if not segment_ids:
                    return

                # Fetch the explicitly requested segments
                stmt = (
                    select(SubtitleSegment)
                    .where(
                        SubtitleSegment.project_id == project_id,
                        SubtitleSegment.id.in_(segment_ids),
                    )
                    .order_by(SubtitleSegment.start_ms)
                )
            else:
                # Fetch all segments, or only untranslated ones
                if retranslate:
                    stmt = (
                        select(SubtitleSegment)
                        .where(SubtitleSegment.project_id == project_id)
                        .order_by(SubtitleSegment.start_ms)
                    )
                else:
                    stmt = (
                        select(SubtitleSegment)
                        .where(
                            SubtitleSegment.project_id == project_id,
                            SubtitleSegment.translated_text.is_(None),
                        )
                        .order_by(SubtitleSegment.start_ms)
                    )

            rows = (await session.execute(stmt)).scalars().all()

            if not rows:
                logger.info(
                    "TranslationService: no untranslated segments for project=%d. "
                    "All segments may already be translated.",
                    project_id,
                )
                await ws_manager.broadcast(
                    project_id,
                    "translation_warning",
                    {"warning": "没有需要翻译的字幕段落（所有字幕可能已翻译完成）"},
                )
                return

            # Warn (but do NOT skip) when source language matches target language.
            # Users may have this misconfigured, or may want to "refine" text using
            # an LLM translator even when source == target.
            same_lang_count = sum(
                1 for seg in rows
                if (seg.original_language or "").lower().split("-")[0] == target_lang
            )
            if same_lang_count == len(rows):
                logger.warning(
                    "TranslationService: all %d segments appear to be in the target "
                    "language %r (project=%d). Proceeding anyway — if results are "
                    "unexpected, check the target_lang setting.",
                    len(rows), target_lang, project_id,
                )

            segments_to_translate: list[SubtitleSegment] = list(rows)
            total = len(segments_to_translate)

            failed_batches = 0

            # Work in batches
            for batch_start in range(0, total, BATCH_SIZE):
                batch = segments_to_translate[batch_start: batch_start + BATCH_SIZE]
                texts = [seg.original_text or "" for seg in batch]

                # Broadcast progress before each batch
                await ws_manager.broadcast(
                    project_id,
                    "translation_progress",
                    {
                        "done": batch_start,
                        "total": total,
                        "progress": round(batch_start / total, 3) if total else 1.0,
                    },
                )

                # Determine source language.
                # Priority: explicit override → first non-empty segment language → "auto"
                if source_lang_override:
                    batch_source_lang = source_lang_override
                else:
                    batch_source_lang = "auto"
                    for seg in batch:
                        lang = (seg.original_language or "").strip()
                        if lang:
                            batch_source_lang = lang
                            break

                try:
                    translated = await translator.translate_batch(
                        texts, source_lang=batch_source_lang
                    )
                except Exception as exc:
                    err_msg = str(exc)
                    logger.error(
                        "TranslationService: batch[%d..%d] error (project=%d): %s",
                        batch_start, batch_start + len(batch), project_id, err_msg,
                    )
                    failed_batches += 1
                    # Use translation_warning (not translation_error) so the
                    # frontend does not abort the progress bar while translation
                    # is still running for the remaining batches.
                    await ws_manager.broadcast(
                        project_id,
                        "translation_warning",
                        {
                            "warning": f"批次 {batch_start+1}–{batch_start+len(batch)} 翻译失败: {err_msg}",
                            "batch_start": batch_start,
                        },
                    )
                    continue  # skip this batch; keep going

                # Check if translator silently returned source text unchanged
                # (common fallback pattern — log a warning so it's visible)
                unchanged = [
                    i for i, (src, dst) in enumerate(zip(texts, translated))
                    if src.strip() and dst.strip() == src.strip()
                ]
                if unchanged:
                    logger.warning(
                        "TranslationService: %d/%d segments in batch[%d] returned "
                        "unchanged text — translator may have failed silently "
                        "(source_lang=%r, provider=%r).",
                        len(unchanged), len(batch), batch_start,
                        batch_source_lang, config.get("provider"),
                    )

                # Persist & broadcast
                for seg, text in zip(batch, translated):
                    seg.translated_text = text

                await session.commit()

                # Broadcast WebSocket events for the batch
                for seg in batch:
                    await ws_manager.broadcast(
                        project_id,
                        "segment_updated",
                        {
                            "id": seg.id,
                            "translated_text": seg.translated_text,
                            "project_id": project_id,
                        },
                    )

        if failed_batches:
            logger.warning(
                "TranslationService: finished project=%d with %d failed batch(es) "
                "out of %d total.",
                project_id, failed_batches, (total + BATCH_SIZE - 1) // BATCH_SIZE,
            )
        else:
            logger.info(
                "TranslationService: translated %d segments for project %d.",
                len(segments_to_translate), project_id,
            )

    async def translate_segment(
        self,
        project_id: int,
        segment_id: int,
    ) -> None:
        """
        Translate a single segment.

        Convenience wrapper around *translate_segments*.
        """
        await self.translate_segments(project_id, [segment_id])

    @classmethod
    async def test_translation(cls, test_text: str = "Hello, world.") -> dict:
        """
        Test the current translation configuration with a single sentence.

        Returns a dict with keys:
            ok       : bool — True if translation succeeded
            provider : str  — configured provider name
            result   : str  — translated text (or empty on error)
            error    : str  — error message (empty on success)
        """
        try:
            config = await cls._load_config()
            translator = get_translator(config)
        except Exception as exc:
            return {
                "ok": False,
                "provider": "unknown",
                "result": "",
                "error": f"初始化翻译器失败: {exc}",
            }

        provider = config.get("provider", "argos")
        try:
            results = await translator.translate_batch([test_text])
            translated = results[0] if results else ""
            if translated.strip() == test_text.strip():
                # Silent fallback: translation returned source unchanged
                return {
                    "ok": False,
                    "provider": provider,
                    "result": translated,
                    "error": (
                        "翻译结果与原文相同，可能存在配置错误或语言包缺失。"
                        "请检查翻译设置（提供商、API Key、语言包）。"
                    ),
                }
            return {"ok": True, "provider": provider, "result": translated, "error": ""}
        except Exception as exc:
            return {
                "ok": False,
                "provider": provider,
                "result": "",
                "error": str(exc),
            }

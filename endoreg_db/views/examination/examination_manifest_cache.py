from __future__ import annotations

from typing import cast

from django.core.cache import cache
from lx_dtypes.models.contracts import JsonObject


class ExaminationManifestCache:
    """Cache-Manager für Examination Manifests"""

    @staticmethod
    def get_cache_key(examination_id: int, language: str = "en") -> str:
        return f"examination_manifest:{examination_id}:{language}"

    @staticmethod
    def get_manifest(examination_id: int, language: str = "en") -> JsonObject | None:
        cache_key = ExaminationManifestCache.get_cache_key(examination_id, language)
        cached_value = cache.get(cache_key)
        return (
            cast(JsonObject, cached_value) if isinstance(cached_value, dict) else None
        )

    @staticmethod
    def set_manifest(
        examination_id: int,
        data: JsonObject,
        language: str = "en",
        timeout: int = 60 * 60,
    ) -> None:
        cache_key = ExaminationManifestCache.get_cache_key(examination_id, language)
        cache.set(cache_key, data, timeout)

    @staticmethod
    def invalidate_manifest(examination_id: int) -> None:
        """Invalidiere Cache für alle Sprachen"""
        for lang in ["en", "de"]:
            cache_key = ExaminationManifestCache.get_cache_key(examination_id, lang)
            cache.delete(cache_key)

# services/lookup_store.py
from __future__ import annotations
import uuid
from typing import Any, Dict, Iterable, Optional
from django.core.cache import cache

DEFAULT_TTL_SECONDS = 60 * 30  # 30 minutes

class LookupStore:
    """
    Server-side lookup dictionary stored in Django cache.
    Return a token to the client; later requests use that token to get/update parts.
    """
    def __init__(self, token: Optional[str] = None):
        self.token = token or uuid.uuid4().hex

    @property
    def cache_key(self) -> str:
        return f"lookup:{self.token}"

    def init(self, initial: Optional[Dict[str, Any]] = None, ttl: int = DEFAULT_TTL_SECONDS) -> str:
        cache.set(self.cache_key, initial or {}, ttl)
        return self.token

    def get_all(self) -> Dict[str, Any]:
        return cache.get(self.cache_key, {})

    def get_many(self, keys: Iterable[str]) -> Dict[str, Any]:
        data = self.get_all()
        return {k: data.get(k) for k in keys}

    def set_many(self, updates: Dict[str, Any], ttl: int = DEFAULT_TTL_SECONDS) -> None:
        data = self.get_all()
        data.update(updates)
        cache.set(self.cache_key, data, ttl)

    def get(self, key: str, default: Any = None) -> Any:
        return self.get_all().get(key, default)

    def set(self, key: str, value: Any, ttl: int = DEFAULT_TTL_SECONDS) -> None:
        data = self.get_all()
        data[key] = value
        cache.set(self.cache_key, data, ttl)

    def delete(self) -> None:
        cache.delete(self.cache_key)

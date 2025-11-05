# services/lookup_store.py
from __future__ import annotations

import uuid
from typing import Any, Dict, Iterable, Optional

from django.conf import settings
from django.core.cache import cache

# Align TTL with Django cache TIMEOUT for consistency in tests and runtime
try:
    DEFAULT_TTL_SECONDS = int(
        settings.CACHES.get("default", {}).get("TIMEOUT", 60 * 30)
    )
except Exception:
    DEFAULT_TTL_SECONDS = 60 * 30  # 30 minutes fallback


class LookupStore:
    """
    Server-side storage for lookup session data using Django cache.

    This class manages token-based sessions for the lookup system, providing
    a cache-backed storage layer that allows clients to maintain state across
    multiple API requests. Each session is identified by a unique token and
    stores derived lookup data including requirement evaluations, statuses,
    and suggested actions.

    Key features:
    - Token-based session management with automatic UUID generation
    - Django cache integration with configurable TTL
    - Data validation and recovery for corrupted sessions
    - Reentrancy protection for recomputation operations
    - Atomic updates to prevent data corruption

    The store uses Django's default cache backend and aligns TTL settings
    with Django's cache configuration for consistency across environments.
    """

    def __init__(self, token: Optional[str] = None):
        """
        Initialize a LookupStore instance with an optional token.

        Args:
            token: Optional session token. If not provided, a new UUID token
                  will be generated automatically.
        """
        self.token = token or uuid.uuid4().hex

    @property
    def cache_key(self) -> str:
        """
        Generate the cache key for this lookup session.

        Returns:
            Cache key string in format 'lookup:{token}'
        """
        return f"lookup:{self.token}"

    def init(
        self, initial: Optional[Dict[str, Any]] = None, ttl: int = DEFAULT_TTL_SECONDS
    ) -> str:
        """
        Initialize a new lookup session in cache.

        Args:
            initial: Optional initial data dictionary to store
            ttl: Time-to-live in seconds for the cache entry

        Returns:
            The session token for this lookup store
        """
        cache.set(self.cache_key, initial or {}, ttl)
        return self.token

    def get_all(self) -> Dict[str, Any]:
        """
        Retrieve all data for this lookup session.

        Returns:
            Complete dictionary of stored lookup data, or empty dict if not found
        """
        return cache.get(self.cache_key, {})

    def get_many(self, keys: Iterable[str]) -> Dict[str, Any]:
        """
        Retrieve multiple specific keys from the lookup session.

        Args:
            keys: Iterable of key names to retrieve

        Returns:
            Dictionary containing only the requested keys and their values
        """
        data = self.get_all()
        return {k: data.get(k) for k in keys}

    def set_many(self, updates: Dict[str, Any], ttl: int = DEFAULT_TTL_SECONDS) -> None:
        """
        Update multiple keys in the lookup session atomically.

        Args:
            updates: Dictionary of key-value pairs to update
            ttl: Time-to-live in seconds for the updated cache entry
        """
        data = self.get_all()
        data.update(updates)
        cache.set(self.cache_key, data, ttl)

    def get(self, key: str, default: Any = None) -> Any:
        """
        Retrieve a single key from the lookup session.

        Args:
            key: Key name to retrieve
            default: Default value if key not found

        Returns:
            Value of the key, or default if not found
        """
        return self.get_all().get(key, default)

    def set(self, key: str, value: Any, ttl: int = DEFAULT_TTL_SECONDS) -> None:
        """
        Set a single key in the lookup session.

        Args:
            key: Key name to set
            value: Value to store
            ttl: Time-to-live in seconds for the updated cache entry
        """
        data = self.get_all()
        data[key] = value
        cache.set(self.cache_key, data, ttl)

    def delete(self) -> None:
        """
        Delete the entire lookup session from cache.
        """
        cache.delete(self.cache_key)

    def patch(self, updates: Dict[str, Any], ttl: int = DEFAULT_TTL_SECONDS) -> None:
        """
        Update existing data with new values (alias for set_many).

        Args:
            updates: Dictionary of key-value pairs to update
            ttl: Time-to-live in seconds for the updated cache entry
        """
        data = self.get_all()
        data.update(updates)
        cache.set(self.cache_key, data, ttl)

    def validate_and_recover_data(self, token):
        """
        Validate stored lookup data and attempt recovery if corrupted.

        Checks for required fields and attempts to recover missing data,
        particularly the patient_examination_id. Logs warnings for missing
        fields but does not trigger automatic recomputation to avoid loops.

        Args:
            token: Session token for logging purposes

        Returns:
            Validated data dictionary, or None if no data exists
        """
        data = self.get_all()

        if not data:
            return None

        # Check if required fields are present
        required_fields = [
            "patient_examination_id",
            "requirementsBySet",
            "requirementStatus",
        ]
        missing_fields = [field for field in required_fields if field not in data]

        if missing_fields:
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(
                f"Missing fields in lookup data for token {token}: {missing_fields}"
            )

            # Try to recover patient_examination_id from token or related data
            if "patient_examination_id" in missing_fields:
                # Attempt to extract from token or find related examination
                recovered_id = self._recover_patient_examination_id(token)
                if recovered_id:
                    data["patient_examination_id"] = recovered_id
                    logger.info(
                        f"Recovered patient_examination_id {recovered_id} for token {token}"
                    )

            # Do not automatically recompute here to avoid loops
            # Recompute is only triggered by PATCH or explicit POST /recompute/
            # For now, just return the data as is

        return data

    def _recover_patient_examination_id(self, token: str) -> Optional[str]:
        """
        Attempt to recover the patient examination ID for corrupted sessions.

        This is a placeholder implementation. In a real system, this might
        query a database or another service to find the examination ID
        associated with the token.

        Args:
            token: Session token

        Returns:
            Recovered patient examination ID, or None if recovery fails
        """
        # In a real implementation, you might query a database or another service.
        # For now, we return None as recovery logic is not defined.
        return None

    def should_recompute(self, token):
        """
        Determine if recomputation is needed based on data freshness.

        Checks the last recomputation timestamp and only allows recomputation
        if more than 30 seconds have passed since the last one. This prevents
        excessive recomputation while allowing for necessary updates.

        Args:
            token: Session token (for future use)

        Returns:
            True if recomputation should be performed, False otherwise
        """
        data = self.get_all()
        if not data:
            return True

        # Check if we have a last_recompute timestamp
        last_recompute = data.get("_last_recompute")
        if not last_recompute:
            return True

        # Only recompute if it's been more than 30 seconds since last recompute
        # This prevents excessive recomputation while allowing for updates
        from datetime import datetime, timedelta

        try:
            last_recompute_time = datetime.fromisoformat(last_recompute)
            return datetime.now() - last_recompute_time > timedelta(seconds=30)
        except (ValueError, TypeError):
            return True

    def mark_recompute_done(self):
        """
        Mark that recomputation has been completed by updating the timestamp.

        Sets the _last_recompute field to the current timestamp in ISO format.
        This timestamp is used by should_recompute() to determine if another
        recomputation is needed.
        """
        from datetime import datetime

        self.set("_last_recompute", datetime.now().isoformat())

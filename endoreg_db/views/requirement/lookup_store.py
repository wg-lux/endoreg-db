# services/lookup_store.py

from __future__ import annotations

import uuid

from typing import Any, Dict, Iterable, Optional

from django.core.cache import cache


from django.conf import settings

from endoreg_db.models.medical import PatientExamination



# Align TTL with Django cache TIMEOUT for consistency in tests and runtime


try:


    DEFAULT_TTL_SECONDS = int(settings.CACHES.get('default', {}).get('TIMEOUT', 60 * 30))


except Exception:


    DEFAULT_TTL_SECONDS = 60 * 30  # 30 minutes fallback



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



    def patch(self, updates: Dict[str, Any], ttl: int = DEFAULT_TTL_SECONDS) -> None:

        """Patch existing data with updates."""

        data = self.get_all()

        data.update(updates)

        cache.set(self.cache_key, data, ttl)





    def validate_and_recover_data(self, token):

        """Validate stored data and attempt recovery if corrupted"""

        data = self.get_all()



        if not data:

            return None



        # Check if required fields are present

        required_fields = ['patient_examination_id', 'requirementsBySet', 'requirementStatus']

        missing_fields = [field for field in required_fields if field not in data]



        if missing_fields:

            import logging

            logger = logging.getLogger(__name__)

            logger.warning(f"Missing fields in lookup data for token {token}: {missing_fields}")



            # Try to recover patient_examination_id from token or related data

            if 'patient_examination_id' in missing_fields:

                # Attempt to extract from token or find related examination

                recovered_id = self._recover_patient_examination_id(token)

                if recovered_id:

                    data['patient_examination_id'] = recovered_id

                    logger.info(f"Recovered patient_examination_id {recovered_id} for token {token}")



            # Do not automatically recompute here to avoid loops


            # Recompute is only triggered by PATCH or explicit POST /recompute/

            # For now, just return the data as is



        return data



    def _recover_patient_examination_id(self, token: str) -> Optional[str]:

        """

        Placeholder for recovering patient_examination_id.

        This should be implemented with logic to find the ID based on the token or other data.

        """

        # In a real implementation, you might query a database or another service.

        # For now, we return None as recovery logic is not defined.

        return None



    def should_recompute(self, token):

        """Check if recomputation is needed based on data freshness"""

        data = self.get_all()

        if not data:

            return True



        # Check if we have a last_recompute timestamp

        last_recompute = data.get('_last_recompute')

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

        """Mark that recomputation has been completed"""

        from datetime import datetime

        self.set('_last_recompute', datetime.now().isoformat())


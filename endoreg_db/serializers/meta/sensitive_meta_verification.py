from __future__ import annotations

import logging
from typing import TypedDict, cast

from rest_framework import serializers

from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.models.state.sensitive_meta import SensitiveMetaState

logger = logging.getLogger(__name__)


class SensitiveMetaVerificationValidatedData(TypedDict):
    sensitive_meta_id: int
    dob_verified: bool | None
    names_verified: bool | None


class SensitiveMetaVerificationSerializer(serializers.Serializer[SensitiveMetaState]):
    """
    Simple serializer for bulk verification state updates.
    Used when only updating verification flags.
    """

    sensitive_meta_id = serializers.IntegerField()
    dob_verified = serializers.BooleanField(required=False)
    names_verified = serializers.BooleanField(required=False)

    _cached_sensitive_meta: SensitiveMeta

    def validate_sensitive_meta_id(self, value: int) -> int:
        """
        Validates that a SensitiveMeta object with the given ID exists and caches it.

        Raises:
            ValidationError: If no SensitiveMeta object is found with the specified ID.
        """
        try:
            # Retrieve and cache the SensitiveMeta instance to avoid a second query in the save method.
            self._cached_sensitive_meta = SensitiveMeta.objects.get(id=value)
            return value
        except SensitiveMeta.DoesNotExist:
            raise serializers.ValidationError(
                f"SensitiveMeta with ID {value} does not exist."
            )

    def save(self, **kwargs: object) -> SensitiveMetaState:
        """
        Updates the verification state for a specified SensitiveMeta instance.

        Uses the cached SensitiveMeta object from validation, obtains or creates its verification state,
        updates the `dob_verified` and `names_verified` fields if provided, and saves the changes.

        Returns:
            The updated verification state object.
        """
        validated_data = cast(
            SensitiveMetaVerificationValidatedData, self.validated_data
        )
        sensitive_meta_id = validated_data["sensitive_meta_id"]
        dob_verified = validated_data["dob_verified"]
        names_verified = validated_data["names_verified"]

        # Use the cached instance from the validation step, avoiding a redundant query.
        sensitive_meta = self._cached_sensitive_meta
        state = sensitive_meta.get_or_create_state()

        if dob_verified is not None:
            state.dob_verified = dob_verified

        if names_verified is not None:
            state.names_verified = names_verified

        state.save()

        logger.info(f"Updated verification state for SensitiveMeta {sensitive_meta_id}")
        return state

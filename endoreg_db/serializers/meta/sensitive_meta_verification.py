from rest_framework import serializers
from django.db import transaction
import logging

from ...models import SensitiveMeta

logger = logging.getLogger(__name__)


class SensitiveMetaVerificationSerializer(serializers.Serializer):
    """
    Simple serializer for bulk verification state updates.
    Used when only updating verification flags.
    """

    sensitive_meta_id = serializers.IntegerField()
    dob_verified = serializers.BooleanField(required=False)
    names_verified = serializers.BooleanField(required=False)

    def validate_sensitive_meta_id(self, value):
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
            raise serializers.ValidationError(f"SensitiveMeta with ID {value} does not exist.")

    @transaction.atomic
    def save(self):
        """
        Updates the verification state for a specified SensitiveMeta instance.

        Uses the SensitiveMeta object from validation, applies select_for_update for strong consistency,
        obtains or creates its verification state, updates the `dob_verified` and `names_verified` fields if provided,
        and saves the changes.

        Returns:
            The updated verification state object.
        """
        sensitive_meta_id = self.validated_data['sensitive_meta_id']
        dob_verified = self.validated_data.get('dob_verified')
        names_verified = self.validated_data.get('names_verified')

        try:
            # Use select_for_update for strong consistency in concurrent environments
            sensitive_meta = SensitiveMeta.objects.select_for_update().get(id=sensitive_meta_id)

            # If cached object from validation matches, use it
            if hasattr(self, "_cached_sensitive_meta") and self._cached_sensitive_meta.id == sensitive_meta_id:
                sensitive_meta = self._cached_sensitive_meta

            state = sensitive_meta.get_or_create_state()

            if dob_verified is not None:
                state.dob_verified = dob_verified

            if names_verified is not None:
                state.names_verified = names_verified

            state.save()

            logger.info(f"Updated verification state for SensitiveMeta ID {sensitive_meta_id}")
            return state

        except SensitiveMeta.DoesNotExist:
            logger.error(f"SensitiveMeta ID {sensitive_meta_id} not found during verification update")
            raise serializers.ValidationError(f"SensitiveMeta with ID {sensitive_meta_id} does not exist.")
        except Exception as e:
            # Log the exception class but not the full details to avoid PII leakage
            logger.error(f"Error updating verification state for ID {sensitive_meta_id}: {type(e).__name__}")
            raise serializers.ValidationError("Failed to update verification state.")

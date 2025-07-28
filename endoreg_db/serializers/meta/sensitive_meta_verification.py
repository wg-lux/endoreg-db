from rest_framework import serializers
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
        Validates that a SensitiveMeta object with the given ID exists.
        
        Raises:
            ValidationError: If no SensitiveMeta object is found with the specified ID.
        """
        try:
            SensitiveMeta.objects.get(id=value)
            return value
        except SensitiveMeta.DoesNotExist:
            raise serializers.ValidationError(f"SensitiveMeta with ID {value} does not exist.")

    def save(self):
        """
        Updates the verification state for a specified SensitiveMeta instance.
        
        Retrieves the SensitiveMeta object by its ID, obtains or creates its verification state, updates the `dob_verified` and `names_verified` fields if provided, and saves the changes.
        
        Returns:
            The updated verification state object.
        
        Raises:
            ValidationError: If the SensitiveMeta does not exist or an error occurs during the update.
        """
        sensitive_meta_id = self.validated_data['sensitive_meta_id']
        dob_verified = self.validated_data.get('dob_verified')
        names_verified = self.validated_data.get('names_verified')
        
        try:
            sensitive_meta = SensitiveMeta.objects.get(id=sensitive_meta_id)
            state = sensitive_meta.get_or_create_state()
            
            if dob_verified is not None:
                state.dob_verified = dob_verified
            
            if names_verified is not None:
                state.names_verified = names_verified
            
            state.save()
            
            logger.info(f"Updated verification state for SensitiveMeta {sensitive_meta_id}")
            return state
            
        except Exception as e:
            logger.error(f"Error updating verification state: {e}")
            raise serializers.ValidationError(f"Failed to update verification state: {e}")
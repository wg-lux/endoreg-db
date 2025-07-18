from rest_framework import serializers
from django.db import transaction
from typing import Dict, Any
import logging

from ..models import SensitiveMeta, SensitiveMetaState, Center, Gender

logger = logging.getLogger(__name__)


class SensitiveMetaDetailSerializer(serializers.ModelSerializer):
    """
    Serializer for displaying SensitiveMeta details with verification state.
    Includes all relevant fields for annotation and verification.
    """
    
    # State verification fields
    is_verified = serializers.SerializerMethodField()
    dob_verified = serializers.SerializerMethodField()
    names_verified = serializers.SerializerMethodField()
    
    # Related fields for better display
    center_name = serializers.CharField(source="center.name", read_only=True)
    patient_gender_name = serializers.CharField(source="patient_gender.name", read_only=True)
    
    # Examiner information
    examiners_display = serializers.SerializerMethodField()
    
    # Formatted dates for display
    patient_dob_display = serializers.SerializerMethodField()
    examination_date_display = serializers.SerializerMethodField()
    
    # Hash displays (last 8 characters for security)
    patient_hash_display = serializers.SerializerMethodField()
    examination_hash_display = serializers.SerializerMethodField()

    class Meta:
        model = SensitiveMeta
        fields = [
            'id',
            'patient_first_name',
            'patient_last_name', 
            'patient_dob',
            'patient_dob_display',
            'examination_date',
            'examination_date_display',
            'center_name',
            'patient_gender_name',
            'endoscope_type',
            'endoscope_sn',
            'patient_hash_display',
            'examination_hash_display',
            'examiners_display',
            'is_verified',
            'dob_verified',
            'names_verified',
        ]
        read_only_fields = [
            'id',
            'patient_hash_display',
            'examination_hash_display',
        ]

    def get_is_verified(self, obj):
        """Get overall verification status."""
        try:
            return obj.is_verified
        except AttributeError:
            return False
        except Exception as e:
            logger.exception(f"Unexpected error in get_is_verified for SensitiveMeta {getattr(obj, 'pk', None)}: {e}")
            raise

    def get_dob_verified(self, obj):
        """Get DOB verification status."""
        try:
            return obj.state.dob_verified
        except Exception:
            return False

    def get_names_verified(self, obj):
        """Get names verification status."""
        try:
            return obj.state.names_verified
        except Exception:
            return False

    def get_examiners_display(self, obj):
        """Get formatted examiner names."""
        try:
            if obj.pk:
                examiners = obj.examiners.all()
                return [f"{e.first_name} {e.last_name}" for e in examiners]
            return []
        except Exception as e:
            logger.warning(f"Error getting examiners for SensitiveMeta {obj.pk}: {e}")
            return []

    def get_patient_dob_display(self, obj):
        """Get formatted DOB for display."""
        if obj.patient_dob:
            return obj.patient_dob.strftime("%Y-%m-%d")
        return None

    def get_examination_date_display(self, obj):
        """Get formatted examination date for display."""
        if obj.examination_date:
            return obj.examination_date.strftime("%Y-%m-%d")
        return None

    def get_patient_hash_display(self, obj):
        """Get truncated patient hash for display."""
        if obj.patient_hash:
            return f"...{obj.patient_hash[-8:]}"
        return None

    def get_examination_hash_display(self, obj):
        """Get truncated examination hash for display."""
        if obj.examination_hash:
            return f"...{obj.examination_hash[-8:]}"
        return None


class SensitiveMetaUpdateSerializer(serializers.ModelSerializer):
    """
    Serializer for updating SensitiveMeta fields including verification state.
    Handles partial updates and state management.
    """
    
    # Verification state fields
    dob_verified = serializers.BooleanField(required=False)
    names_verified = serializers.BooleanField(required=False)
    
    # Center can be updated by name
    center_name = serializers.CharField(write_only=True, required=False)
    
    # Gender can be updated by name
    patient_gender_name = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = SensitiveMeta
        fields = [
            'patient_first_name',
            'patient_last_name',
            'patient_dob',
            'examination_date',
            'center_name',
            'patient_gender_name',
            'endoscope_type',
            'endoscope_sn',
            'examiner_first_name',
            'examiner_last_name',
            'dob_verified',
            'names_verified',
        ]

    def validate_center_name(self, value):
        """Validate center exists and return the instance."""
        if value:
            try:
                # Return the instance to avoid double query in update()
                return Center.objects.get_by_natural_key(value)
            except Center.DoesNotExist:
                raise serializers.ValidationError(f"Center '{value}' does not exist.")
        return value

    def validate_patient_gender_name(self, value):
        """Validate gender exists and return the instance."""
        if value:
            try:
                # Return the instance to avoid double query in update()
                return Gender.objects.get(name=value)
            except Gender.DoesNotExist:
                raise serializers.ValidationError(f"Gender '{value}' does not exist.")
        return value

    def validate(self, data):
        """Custom validation for the entire data set."""
        # Guard against None values before calling strip()
        first_name = data.get('patient_first_name')
        if first_name is not None and not first_name.strip():
            raise serializers.ValidationError({
                'patient_first_name': 'First name cannot be empty.'
            })
        
        last_name = data.get('patient_last_name')
        if last_name is not None and not last_name.strip():
            raise serializers.ValidationError({
                'patient_last_name': 'Last name cannot be empty.'
            })

        return data

    @transaction.atomic
    def update(self, instance, validated_data):
        """
        Update SensitiveMeta instance and its related state.
        Handles both model fields and verification state.
        """
        # Extract verification state data
        dob_verified = validated_data.pop('dob_verified', None)
        names_verified = validated_data.pop('names_verified', None)
        
        # -- center -------------------------------------------------
        center = validated_data.pop('center_name', None)
        if isinstance(center, Center):          # returned by validate_center_name
            instance.center = center

        # -- gender -------------------------------------------------
        gender = validated_data.pop('patient_gender_name', None)
        if isinstance(gender, Gender):          # returned by validate_patient_gender_name
            instance.patient_gender = gender

        # -- ordinary fields ---------------------------------------
        if validated_data:
            instance.update_from_dict(validated_data)   # should NOT call save()

        # -- verification state ------------------------------------
        if dob_verified is not None or names_verified is not None:
            state = instance.get_or_create_state()
            if dob_verified is not None:
                state.dob_verified = dob_verified
                logger.info(f"Updated DOB verification for SensitiveMeta {instance.pk}: {dob_verified}")
            if names_verified is not None:
                state.names_verified = names_verified
                logger.info(f"Updated names verification for SensitiveMeta {instance.pk}: {names_verified}")
            state.save()

        # -- finally persist the model itself ----------------------
        instance.save()
        return instance


class SensitiveMetaVerificationSerializer(serializers.Serializer):
    """
    Simple serializer for bulk verification state updates.
    Used when only updating verification flags.
    """
    
    sensitive_meta_id = serializers.IntegerField()
    dob_verified = serializers.BooleanField(required=False)
    names_verified = serializers.BooleanField(required=False)
    
    def validate_sensitive_meta_id(self, value):
        """Ensure SensitiveMeta exists."""
        try:
            SensitiveMeta.objects.get(id=value)
            return value
        except SensitiveMeta.DoesNotExist:
            raise serializers.ValidationError(f"SensitiveMeta with ID {value} does not exist.")

    @transaction.atomic
    def save(self):
        """Update verification state for the specified SensitiveMeta with proper locking."""
        sensitive_meta_id = self.validated_data['sensitive_meta_id']
        dob_verified = self.validated_data.get('dob_verified')
        names_verified = self.validated_data.get('names_verified')
        
        try:
            # Use select_for_update for strong consistency in concurrent environments
            sensitive_meta = SensitiveMeta.objects.select_for_update().get(id=sensitive_meta_id)
            state = sensitive_meta.get_or_create_state()
            
            if dob_verified is not None:
                state.dob_verified = dob_verified
            
            if names_verified is not None:
                state.names_verified = names_verified
            
            state.save()
            
            # Only log the ID to avoid potential PII leakage
            logger.info(f"Updated verification state for SensitiveMeta ID {sensitive_meta_id}")
            return state
            
        except SensitiveMeta.DoesNotExist:
            logger.error(f"SensitiveMeta ID {sensitive_meta_id} not found during verification update")
            raise serializers.ValidationError(f"SensitiveMeta with ID {sensitive_meta_id} does not exist.")
        except Exception as e:
            # Log the exception class but not the full details to avoid PII leakage
            logger.error(f"Error updating verification state for ID {sensitive_meta_id}: {type(e).__name__}")
            raise serializers.ValidationError("Failed to update verification state.")
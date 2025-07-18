from rest_framework import serializers
from django.db import transaction
from typing import Dict, Any
import logging

from ...models import SensitiveMeta, SensitiveMetaState, Center, Gender

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
        """Validate center exists."""
        if value:
            try:
                Center.objects.get_by_natural_key(value)
                return value
            except Center.DoesNotExist:
                raise serializers.ValidationError(f"Center '{value}' does not exist.")
        return value

    def validate_patient_gender_name(self, value):
        """Validate gender exists."""
        if value:
            try:
                Gender.objects.get(name=value)
                return value
            except Gender.DoesNotExist:
                raise serializers.ValidationError(f"Gender '{value}' does not exist.")
        return value

    def validate(self, data):
        """Custom validation for the entire data set."""
        # Ensure names are not empty if provided
        if 'patient_first_name' in data and not data['patient_first_name'].strip():
            raise serializers.ValidationError({
                'patient_first_name': 'First name cannot be empty.'
            })
        
        if 'patient_last_name' in data and not data['patient_last_name'].strip():
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
        
        # Extract and handle center update
        center_name = validated_data.pop('center_name', None)
        if center_name:
            try:
                center = Center.objects.get_by_natural_key(center_name)
                instance.center = center
            except Center.DoesNotExist:
                logger.error(f"Center '{center_name}' not found during update")
        
        # Extract and handle gender update
        patient_gender_name = validated_data.pop('patient_gender_name', None)
        if patient_gender_name:
            try:
                gender = Gender.objects.get(name=patient_gender_name)
                instance.patient_gender = gender
            except Gender.DoesNotExist:
                logger.error(f"Gender '{patient_gender_name}' not found during update")

        # Update regular fields using the model's update_from_dict method
        if validated_data:
            instance.update_from_dict(validated_data)
        
        # Update verification state if provided
        if dob_verified is not None or names_verified is not None:
            # Ensure state exists
            state = instance.get_or_create_state()
            
            if dob_verified is not None:
                state.dob_verified = dob_verified
                logger.info(f"Updated DOB verification for SensitiveMeta {instance.pk}: {dob_verified}")
            
            if names_verified is not None:
                state.names_verified = names_verified
                logger.info(f"Updated names verification for SensitiveMeta {instance.pk}: {names_verified}")
            
            state.save()

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

    def save(self):
        """Update verification state for the specified SensitiveMeta."""
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
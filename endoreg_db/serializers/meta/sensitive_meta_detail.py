from rest_framework import serializers
import logging

from ...models import SensitiveMeta

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


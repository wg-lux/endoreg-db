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

    # Related fields
    center_name = serializers.CharField(source="center.name", read_only=True)
    patient_gender_name = serializers.CharField(source="patient_gender.name", read_only=True)
    examiners_display = serializers.SerializerMethodField()

    # Formatted display fields
    patient_dob_display = serializers.SerializerMethodField()
    examination_date_display = serializers.SerializerMethodField()

    # Hash displays (last 8 chars)
    patient_hash_display = serializers.SerializerMethodField()
    examination_hash_display = serializers.SerializerMethodField()

    # Text fields
    text = serializers.SerializerMethodField()
    anonymized_text = serializers.SerializerMethodField()

    class Meta:
        model = SensitiveMeta
        fields = [
            "id",
            "casenumber",
            "patient_first_name",
            "patient_last_name",
            "patient_dob",
            "patient_dob_display",
            "examination_date",
            "examination_date_display",
            "examination_time",
            "center_name",
            "patient_gender_name",
            "endoscope_type",
            "endoscope_sn",
            "patient_hash_display",
            "examination_hash_display",
            "examiners_display",
            "is_verified",
            "dob_verified",
            "names_verified",
            "text",
            "anonymized_text",
            "external_id",
            "external_id_origin"
        ]
        read_only_fields = [
            "id",
            "patient_hash_display",
            "examination_hash_display",
        ]

    # --- Verification getters ---
    def get_is_verified(self, obj): return getattr(obj, "is_verified", False)
    def get_dob_verified(self, obj): return getattr(getattr(obj, "state", None), "dob_verified", False)
    def get_names_verified(self, obj): return getattr(getattr(obj, "state", None), "names_verified", False)

    # --- Examiner display ---
    def get_examiners_display(self, obj):
        try:
            return [f"{e.first_name} {e.last_name}" for e in obj.examiners.all()] if obj.pk else []
        except Exception as e:
            logger.warning(f"Error fetching examiners for SensitiveMeta {obj.pk}: {e}")
            return []

    # --- Date formatters ---
    def get_patient_dob_display(self, obj):
        return obj.patient_dob.strftime("%Y-%m-%d") if obj.patient_dob else None

    def get_examination_date_display(self, obj):
        return obj.examination_date.strftime("%Y-%m-%d") if obj.examination_date else None

    # --- Hash short forms ---
    def get_patient_hash_display(self, obj):
        return f"...{obj.patient_hash[-8:]}" if obj.patient_hash else None

    def get_examination_hash_display(self, obj):
        return f"...{obj.examination_hash[-8:]}" if obj.examination_hash else None

    # --- Text fields ---
    def get_text(self, obj):
        return obj.text if isinstance(obj.text, str) else None

    def get_anonymized_text(self, obj):
        return obj.anonymized_text if isinstance(obj.anonymized_text, str) else None
    
    def get_external_id(self, obj) -> str | None:  
        return obj.external_id if isinstance(obj.external_id, str) else None  
    
    def get_external_id_origin(self, obj) -> str | None:  
        return obj.external_id_origin if isinstance(obj.external_id_origin, str) else None 


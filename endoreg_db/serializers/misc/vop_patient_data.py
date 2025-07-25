from endoreg_db.models.media import RawPdfFile, VideoFile


from rest_framework import serializers

#TODO add this "naming convention" to the documentation
# VoP: Video or Pdf

class VoPPatientDataSerializer(serializers.Serializer):
    """
    Serializer that converts a VideoFile or RawPdfFile to the structure 
    the Pinia store needs for validation workflow.
    """

    # Mark all fields as read_only since they're computed in to_representation
    id = serializers.IntegerField(read_only=True)
    sensitiveMetaId = serializers.IntegerField(read_only=True)
    text = serializers.CharField(read_only=True)
    anonymizedText = serializers.CharField(read_only=True)
    reportMeta = serializers.JSONField(read_only=True)
    status = serializers.CharField(read_only=True)
    error = serializers.BooleanField(read_only=True)

    def to_representation(self, instance):
        """
        Handles both VideoFile and RawPdfFile instances.
        """
        if isinstance(instance, VideoFile):
            # For videos, we don't have text content in the traditional sense
            # But we can extract metadata text from sensitive_meta
            text = ""
            anonym_text = ""

            if instance.sensitive_meta:
                sm = instance.sensitive_meta
                # Create a structured text representation similar to FileOverviewSerializer
                text_parts = []

                if sm.patient_first_name or sm.patient_last_name:
                    patient_name = f"{sm.patient_first_name or ''} {sm.patient_last_name or ''}".strip()
                    text_parts.append(f"Patient: {patient_name}")

                if sm.patient_dob:
                    text_parts.append(f"Date of Birth: {sm.patient_dob.date()}")

                if sm.examination_date:
                    text_parts.append(f"Examination Date: {sm.examination_date}")

                if sm.center:
                    text_parts.append(f"Center: {sm.center.name}")

                text = "\n".join(text_parts)

                # Create anonymized version
                anonym_text = text
                if sm.patient_first_name:
                    anonym_text = anonym_text.replace(sm.patient_first_name, "[FIRST_NAME]")
                if sm.patient_last_name:
                    anonym_text = anonym_text.replace(sm.patient_last_name, "[LAST_NAME]")
                if sm.patient_dob:
                    anonym_text = anonym_text.replace(str(sm.patient_dob.date()), "[DOB]")

            return {
                "id": instance.pk,
                "sensitiveMetaId": instance.sensitive_meta.pk if instance.sensitive_meta else None,
                "text": text,
                "anonymizedText": anonym_text,
                "reportMeta": self._serialize_sensitive_meta(instance.sensitive_meta) if instance.sensitive_meta else None,
                "status": "processing" if instance.state and instance.state.frames_extracted else "not_started",
                "error": False,
            }

        elif isinstance(instance, RawPdfFile):
            return {
                "id": instance.pk,
                "sensitiveMetaId": instance.sensitive_meta.pk if instance.sensitive_meta else None,
                "text": instance.text or "",
                "anonymizedText": instance.anonymized_text or "",
                "reportMeta": self._serialize_sensitive_meta(instance.sensitive_meta) if instance.sensitive_meta else None,
                "status": "done" if instance.anonymized_text else "not_started",
                "error": False,
            }

        else:
            raise TypeError(f"Unsupported instance for PatientDataSerializer: {type(instance)}")

    def _serialize_sensitive_meta(self, sensitive_meta):
        """Helper method to serialize SensitiveMeta to match the expected format"""
        if not sensitive_meta:
            return None

        return {
            "id": sensitive_meta.pk,
            "patientFirstName": sensitive_meta.patient_first_name or "",
            "patientLastName": sensitive_meta.patient_last_name or "",
            "patientDob": sensitive_meta.patient_dob.isoformat() if sensitive_meta.patient_dob else "",
            "patientGender": str(sensitive_meta.patient_gender) if sensitive_meta.patient_gender else "",
            "examinationDate": sensitive_meta.examination_date.isoformat() if sensitive_meta.examination_date else "",
            "centerName": sensitive_meta.center.name if sensitive_meta.center else "",
            "endoscopeType": sensitive_meta.endoscope_type or "",
            "endoscopeSn": sensitive_meta.endoscope_sn or "",
            "isVerified": getattr(sensitive_meta, "is_verified", False),
        }
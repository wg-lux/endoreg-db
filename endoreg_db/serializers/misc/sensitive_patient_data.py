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
        Serialize a VideoFile or RawPdfFile instance into a structured dictionary for validation workflows.
        
        Depending on the instance type, constructs a dictionary containing identifiers, sensitive metadata, text summaries, anonymized text, processing status, and error flag. For VideoFile instances, generates a text summary from associated sensitive metadata and anonymizes personal identifiers. For RawPdfFile instances, uses the instance's text fields directly. Raises a TypeError if the instance is neither type.
        
        Parameters:
            instance: A VideoFile or RawPdfFile model instance to serialize.
        
        Returns:
            dict: A structured representation of the instance suitable for Pinia store consumption.
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
            # Generate PDF streaming URL using pdf_id (RawPdfFile.id)
            pdf_stream_url = f"/api/media/pdfs/{instance.pk}/stream/"
            
            return {
                "id": instance.pk,
                "sensitiveMetaId": instance.sensitive_meta.pk if instance.sensitive_meta else None,
                "text": instance.text or "",
                "anonymizedText": instance.anonymized_text or "",
                "reportMeta": self._serialize_sensitive_meta(instance.sensitive_meta) if instance.sensitive_meta else None,
                "status": "done" if instance.anonymized_text else "not_started",
                "error": False,
                "pdfStreamUrl": pdf_stream_url,
            }

        else:
            raise TypeError(f"Unsupported instance for VoPPatientDataSerializer: {type(instance)}")

    def _serialize_sensitive_meta(self, sensitive_meta):
        """
        Serialize a SensitiveMeta object into a dictionary with patient and examination details.
        
        Returns:
            dict or None: A dictionary containing patient and examination metadata fields, or None if sensitive_meta is not provided.
        """
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
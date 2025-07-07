from rest_framework import serializers
from typing import TYPE_CHECKING
from endoreg_db.models.media import VideoFile, RawPdfFile

if TYPE_CHECKING:
    pass

class FileOverviewSerializer(serializers.Serializer):
    """
    Polymorphic "union" serializer – we normalise both model types
    (VideoFile, RawPdfFile) into the data structure the Vue store needs.
    """

    # --- fields expected by the front-end ---------------------------
    # All fields are read_only since they're computed in to_representation
    id = serializers.IntegerField(read_only=True)
    filename = serializers.CharField(read_only=True)
    mediaType = serializers.CharField(read_only=True)
    anonymizationStatus = serializers.CharField(read_only=True)
    annotationStatus = serializers.CharField(read_only=True)
    createdAt = serializers.DateTimeField(read_only=True)
    text = serializers.CharField(required=False, allow_blank=True, read_only=True)
    anonymizedText = serializers.CharField(required=False, allow_blank=True, read_only=True)

    # --- converting DB objects to that shape -----------------------
    def to_representation(self, instance):
        """
        Handles both VideoFile and RawPdfFile instances.
        """
        text = ""
        anonym_text = ""
        
        if isinstance(instance, VideoFile):
            media_type = "video"
            created_at = instance.uploaded_at
            filename = instance.original_file_name or (
                instance.raw_file.name.split("/")[-1] if instance.raw_file else "unknown"
            )
            
            # ------- anonymization status using VideoState model
            vs = instance.state
            if vs is None:
                anonym_status = "not_started"
            elif hasattr(vs, 'processing_error') and getattr(vs, 'processing_error', False):
                anonym_status = "failed"
            elif vs.anonymized:
                anonym_status = "done"
            elif vs.frames_extracted:
                anonym_status = "processing"
            else:
                anonym_status = "not_started"
            
            # ------- annotation status (validated label segments)
            if instance.label_video_segments.filter(state__is_validated=True).exists():
                annot_status = "done"
            else:
                annot_status = "not_started"
            
            # ------- Extract text from sensitive_meta for videos
            if instance.sensitive_meta:
                sm = instance.sensitive_meta
                # Create a structured text representation from sensitive meta
                text_parts = []
                
                # Patient information
                if sm.patient_first_name or sm.patient_last_name:
                    patient_name = f"{sm.patient_first_name or ''} {sm.patient_last_name or ''}".strip()
                    text_parts.append(f"Patient: {patient_name}")
                
                if sm.patient_dob:
                    text_parts.append(f"Date of Birth: {sm.patient_dob.date()}")
                
                if sm.patient_gender:
                    text_parts.append(f"Gender: {sm.patient_gender}")
                
                # Examination information
                if sm.examination_date:
                    text_parts.append(f"Examination Date: {sm.examination_date}")
                
                if sm.center:
                    text_parts.append(f"Center: {sm.center.name}")
                
                # Equipment information
                if sm.endoscope_type:
                    text_parts.append(f"Endoscope Type: {sm.endoscope_type}")
                
                if sm.endoscope_sn:
                    text_parts.append(f"Endoscope SN: {sm.endoscope_sn}")
                
                # Examiner information
                if sm.pk:  # Only if saved
                    try:
                        examiners = list(sm.examiners.all())
                        if examiners:
                            examiner_names = ", ".join(str(e) for e in examiners)
                            text_parts.append(f"Examiners: {examiner_names}")
                    except Exception:
                        pass  # Ignore examiner lookup errors
                
                text = "\n".join(text_parts)
                
                # Create anonymized version by replacing sensitive data
                anonym_text = text
                if sm.patient_first_name:
                    anonym_text = anonym_text.replace(sm.patient_first_name, "[FIRST_NAME]")
                if sm.patient_last_name:
                    anonym_text = anonym_text.replace(sm.patient_last_name, "[LAST_NAME]")
                if sm.patient_dob:
                    anonym_text = anonym_text.replace(str(sm.patient_dob.date()), "[DOB]")
                if sm.endoscope_sn:
                    anonym_text = anonym_text.replace(sm.endoscope_sn, "[ENDOSCOPE_SN]")
                
                # Replace examiner names if available
                if sm.pk:
                    try:
                        examiners = list(sm.examiners.all())
                        for examiner in examiners:
                            anonym_text = anonym_text.replace(str(examiner), "[EXAMINER]")
                    except Exception:
                        pass

        elif isinstance(instance, RawPdfFile):
            media_type = "pdf"
            created_at = instance.created_at
            filename = instance.file.name.split("/")[-1] if instance.file else "unknown"
            # Check anonymized_text field
            anonym_status = "done" if (instance.anonymized_text and instance.anonymized_text.strip()) else "not_started"
            # PDF annotation == "sensitive meta validated"
            annot_status = "done" if getattr(instance.sensitive_meta, "is_verified", False) else "not_started"
            
            # Extract text content from PDF
            text = instance.text or ""
            anonym_text = instance.anonymized_text or ""

        else:  # shouldn't happen
            raise TypeError(f"Unsupported instance for overview: {type(instance)}")

        return {
            "id": instance.pk,
            "filename": filename,
            "mediaType": media_type,
            "anonymizationStatus": anonym_status,
            "annotationStatus": annot_status,
            "createdAt": created_at,
            "text": text,
            "anonymizedText": anonym_text,
        }


class PatientDataSerializer(serializers.Serializer):
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
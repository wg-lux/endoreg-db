from rest_framework import serializers
from typing import TYPE_CHECKING
from endoreg_db.models.media import VideoFile, RawPdfFile
from endoreg_db.models.state.video import AnonymizationStatus as VideoAnonymizationStatus
from endoreg_db.models.state.raw_pdf import AnonymizationStatus as PdfAnonymizationStatus

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
        Return a unified dictionary representation of either a VideoFile or RawPdfFile instance for front-end use.
        
        For VideoFile instances, extracts and structures metadata such as patient, examination, equipment, and examiner information, and generates an anonymized version of the text by replacing sensitive fields with placeholders. For RawPdfFile instances, extracts text and anonymized text directly and determines statuses based on available fields.
        
        Parameters:
            instance: A VideoFile or RawPdfFile object to be serialized.
        
        Returns:
            dict: A normalized dictionary containing id, filename, mediaType, anonymizationStatus, annotationStatus, createdAt, text, and anonymizedText fields.
        
        Raises:
            TypeError: If the instance is not a VideoFile or RawPdfFile.
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
            anonym_status = vs.anonymization_status if vs else VideoAnonymizationStatus.NOT_STARTED
            
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
            instance:RawPdfFile
            media_type = "pdf"
            created_at = instance.date_created
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

# api/serializers/file_overview.py
from rest_framework import serializers
from django.conf import settings
from pathlib import Path
from endoreg_db.models import VideoFile, RawPdfFile


class FileOverviewSerializer(serializers.Serializer):
    """
    Polymorphic "union" serializer – we normalise both model types
    (VideoFile, RawPdfFile) into the data structure the Vue store needs.
    """

    # --- fields expected by the front-end ---------------------------
    id = serializers.IntegerField()
    filename = serializers.CharField()
    mediaType = serializers.CharField(source="media_type")
    anonymizationStatus = serializers.CharField(source="anonym_status")
    annotationStatus = serializers.CharField(source="annot_status")
    createdAt = serializers.DateTimeField(source="created_at")
    text = serializers.CharField(required=False, allow_blank=True)
    anonymizedText = serializers.CharField(
        source="anonymized_text", required=False, allow_blank=True
    )

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
        filename = instance.processed_video_hash or (
            instance.get_raw_file_path.name.split("/")[-1] if instance.raw_file else "unknown"
        )
        
        # ------- anonymization status (adjust to your VideoState model)
        vs = instance.state
        if vs is None:
            anonym_status = "not_started"
        elif hasattr(vs, 'processing_error') and vs.processing_error:
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
        # ------- anonymization status (adjust to your VideoState model)
        vs = instance.state
        if vs is None:
            anonym_status = "not_started"
        elif hasattr(vs, 'processing_error') and vs.processing_error:
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
        
        # Videos don't have text content, but we could potentially extract 
        # text from the sensitive_meta if needed
        # For now, keep them empty as per the original requirement
        text = ""
        anonym_text = ""

    elif isinstance(instance, RawPdfFile):
        media_type = "pdf"
        created_at = instance.created_at
        filename = instance.file.name.split("/")[-1] if instance.file else "unknown"
        # Fix: Check anonymized_text field instead of non-existent anonymized field
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

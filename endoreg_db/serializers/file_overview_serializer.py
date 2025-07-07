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
            text = instance.text or ""
            anonym_text = instance.anonymized_text or ""
            

        elif isinstance(instance, RawPdfFile):
            media_type = "pdf"
            created_at = instance.created_at
            filename = instance.file.name.split("/")[-1] if instance.file else "unknown"
            # Fix: Check anonymized_text field instead of non-existent anonymized field
            anonym_status = "done" if (instance.anonymized_text and instance.anonymized_text.strip()) else "not_started"
            # PDF annotation == "sensitive meta validated"
            annot_status = "done" if getattr(instance.sensitive_meta, "is_verified", False) else "not_started"
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

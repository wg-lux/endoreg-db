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
            

        elif isinstance(instance, RawPdfFile):
            media_type = "pdf"
            created_at = instance.created_at
            filename = instance.file.name.split("/")[-1] if instance.file else "unknown"
            # Fix: Check anonymized_text field instead of non-existent anonymized field
            anonym_status = "done" if (instance.anonymized_text and instance.anonymized_text.strip()) else "not_started"
            # PDF annotation == "sensitive meta validated"
            annot_status = "done" if getattr(instance.sensitive_meta, "is_verified", False) else "not_started"
    

        else:  # shouldn't happen
            raise TypeError(f"Unsupported instance for overview: {type(instance)}")

        return {
            "id": instance.pk,
            "filename": filename,
            "mediaType": media_type,
            "anonymizationStatus": anonym_status,
            "annotationStatus": annot_status,
            "createdAt": created_at,
        }


class PatientDataSerializer(serializers.Serializer):
    """
    Serializer that converts a VideoFile or RawPdfFile to the structure 
    the Pinia store needs for validation workflow.
    """
    
    def to_representation(self, obj):
        """
        Convert VideoFile or RawPdfFile instance to PatientData format.
        """
        request = self.context.get('request')
        
        # Determine if this is a video or PDF
        is_video = isinstance(obj, VideoFile)
        
        # Base patient data structure
        patient_data = {
            'id': obj.id,
            'sensitiveMetaId': obj.sensitive_meta.id if obj.sensitive_meta else None,
        }
        
        if is_video:
            # Video-specific data
            patient_data.update({
                'text': '',  # Videos don't have text
                'anonymizedText': '',  # Videos don't have anonymized text
                'reportMeta': {
                    'id': obj.sensitive_meta.id if obj.sensitive_meta else None,
                    'patientFirstName': obj.sensitive_meta.patient_first_name if obj.sensitive_meta else '',
                    'patientLastName': obj.sensitive_meta.patient_last_name if obj.sensitive_meta else '',
                    'patientDob': obj.sensitive_meta.patient_dob if obj.sensitive_meta else '',
                    'patientGender': obj.sensitive_meta.patient_gender if obj.sensitive_meta else '',
                    'examinationDate': obj.sensitive_meta.examination_date if obj.sensitive_meta else '',
                    'casenumber': getattr(obj.sensitive_meta, 'casenumber', '') if obj.sensitive_meta else '',
                    'file': request.build_absolute_uri(f"/api/videostream/{obj.id}/") if request else None,
                    'pdfUrl': None,  # Videos don't have PDF URLs
                    'fullPdfPath': None
                }
            })
        else:
            # PDF-specific data (RawPdfFile)
            patient_data.update({
                'text': obj.text or '',
                'anonymizedText': obj.anonymized_text or '',
                'reportMeta': {
                    'id': obj.sensitive_meta.id if obj.sensitive_meta else None,
                    'patientFirstName': obj.sensitive_meta.patient_first_name if obj.sensitive_meta else '',
                    'patientLastName': obj.sensitive_meta.patient_last_name if obj.sensitive_meta else '',
                    'patientDob': obj.sensitive_meta.patient_dob if obj.sensitive_meta else '',
                    'patientGender': obj.sensitive_meta.patient_gender if obj.sensitive_meta else '',
                    'examinationDate': obj.sensitive_meta.examination_date if obj.sensitive_meta else '',
                    'casenumber': getattr(obj.sensitive_meta, 'casenumber', '') if obj.sensitive_meta else '',
                    'file': None,  # PDFs don't have video files
                    'pdfUrl': request.build_absolute_uri(obj.file.url) if request and obj.file else None,
                    'fullPdfPath': str(Path(settings.MEDIA_ROOT) / obj.file.name) if obj.file else None
                }
            })
        
        return patient_data

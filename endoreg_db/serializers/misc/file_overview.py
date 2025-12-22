from typing import TYPE_CHECKING

from rest_framework import serializers

from endoreg_db.models.media import RawPdfFile, VideoFile
from endoreg_db.models.state.anonymization import AnonymizationState

if TYPE_CHECKING:
    pass

class FileOverviewSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    filename = serializers.CharField(read_only=True)
    mediaType = serializers.CharField(read_only=True)
    anonymizationStatus = serializers.CharField(read_only=True)
    annotationStatus = serializers.CharField(read_only=True)
    createdAt = serializers.DateTimeField(read_only=True)
    sensitiveMetaId = serializers.IntegerField(read_only=True, allow_null=True)
    fileSize = serializers.IntegerField(read_only=True, required=False)
    
    def to_representation(self, instance):
        # 1. Extract Type-Specific Data
        if isinstance(instance, VideoFile):
            media_type = "video"
            filename = instance.original_file_name or (
                instance.raw_file.name.split("/")[-1] if instance.raw_file else "unknown_video"
            )
            created_at = instance.uploaded_at
            # Use the state relation optimized in the View
            state_obj = instance.state
            sensitive_meta = instance.sensitive_meta
            file_size = instance.raw_file.size if instance.raw_file else 0

        elif isinstance(instance, RawPdfFile):
            media_type = "pdf"
            filename = instance.file.name.split("/")[-1] if instance.file else "unknown_report"
            created_at = instance.date_created
            state_obj = instance.state
            sensitive_meta = instance.sensitive_meta
            file_size = instance.file.size if instance.file else 0

        else:
            raise TypeError(f"Unexpected object type: {type(instance)}")

        # 2. Determine Status (Single Source of Truth: The State Model)
        # This uses the @property .anonymization_status from VideoState/RawPdfState
        raw_status = state_obj.anonymization_status if state_obj else AnonymizationState.NOT_STARTED

        # 3. Map to Frontend 'annotationStatus'
        annot_status = "not_started"
        
        # FIX: Explicitly check against the Enum value
        if raw_status == AnonymizationState.VALIDATED:
            annot_status = "validated"
        
        # 4. Return Payload
        return {
            "id": instance.pk,
            "filename": filename,
            "mediaType": media_type,
            "anonymizationStatus": raw_status,
            "annotationStatus": annot_status,
            "createdAt": created_at,
            "sensitiveMetaId": sensitive_meta.pk if sensitive_meta else None,
            "fileSize": file_size,
        }
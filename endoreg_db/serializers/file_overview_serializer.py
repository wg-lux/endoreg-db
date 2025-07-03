# api/serializers/file_overview.py
from rest_framework import serializers
from endoreg_db.models import VideoFile, RawPdfFile


class FileOverviewSerializer(serializers.Serializer):
    """
    Polymorphic “union” serializer – we normalise both model types
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
            if vs is None or not vs.frames_extracted:
                anonym_status = "not_started"
            elif vs.anonymized:                          # fully processed
                anonym_status = "done"
            elif not vs.sensitive_meta_processed:                          # <-- if you track that
                anonym_status = "processing"
            else:
                anonym_status = "processing"             # safe default
            # ------- annotation status (validated label segments)
            if instance.label_video_segments.filter(state__is_validated=True).exists():
                annot_status = "done"
            else:
                annot_status = "not_started"
            

        elif isinstance(instance, RawPdfFile):
            media_type = "pdf"
            created_at = instance.uploaded_at
            filename = instance.file.name.split("/")[-1] if instance.file else "unknown"
            anonym_status = "done" if instance.anonymized else "not_started"
            # PDF annotation == “sensitive meta validated”
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

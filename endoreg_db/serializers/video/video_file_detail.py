from rest_framework import serializers
from django.conf import settings
from pathlib import Path

from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.state.video_segment_validation import (
    post_validation_rebuild_summary,
    resolve_segment_annotation_status,
    segment_annotations_are_final,
)
from endoreg_db.serializers.video.video_file_brief import VideoBriefSerializer
from ...utils.video.calc_duration_seconds import _calc_duration_vf


class VideoDetailSerializer(VideoBriefSerializer):
    # pull selected fields from SensitiveMeta (READ-ONLY) - using SerializerMethodField to handle datetime->date conversion
    patient_first_name = serializers.CharField(
        source="sensitive_meta.patient_first_name", read_only=True
    )
    patient_last_name = serializers.CharField(
        source="sensitive_meta.patient_last_name", read_only=True
    )
    patient_dob = serializers.SerializerMethodField()
    examination_date = serializers.SerializerMethodField()

    file = serializers.SerializerMethodField()
    full_path = serializers.SerializerMethodField()
    duration = serializers.SerializerMethodField()
    segment_annotations_validated = serializers.SerializerMethodField()
    segment_annotation_status = serializers.SerializerMethodField()
    outside_segments_removed = serializers.SerializerMethodField()
    post_validation_rebuild = serializers.SerializerMethodField()

    class Meta(VideoBriefSerializer.Meta):
        fields = VideoBriefSerializer.Meta.fields + [
            "file",
            "full_path",
            "patient_first_name",
            "patient_last_name",
            "patient_dob",
            "examination_date",
            "duration",
            "export_segments_by_video",
            "segment_annotations_validated",
            "segment_annotation_status",
            "outside_segments_removed",
            "post_validation_rebuild",
        ]

    # ---------- helpers ---------- #
    def get_file(self, obj):
        f = obj.processed_file or obj.raw_file
        return f.name if f else None

    def get_full_path(self, obj):
        f = obj.processed_file or obj.raw_file
        return str(Path(settings.MEDIA_ROOT) / f.name) if f else None

    def get_duration(self, obj: VideoFile):
        """
        Return the duration of the video, using the stored value if available or calculating it if not.

        Parameters:
            obj (VideoFile): The video file instance.

        Returns:
            float or None: Duration of the video in seconds, or None if unavailable.
        """
        return obj.duration or _calc_duration_vf(obj)

    def get_segment_annotations_validated(self, obj: VideoFile) -> bool:
        return bool(segment_annotations_are_final(obj))

    def get_segment_annotation_status(self, obj: VideoFile) -> str:
        return resolve_segment_annotation_status(obj)

    def get_outside_segments_removed(self, obj: VideoFile) -> bool:
        state = getattr(obj, "state", None)
        if state is None:
            return False
        return bool(getattr(state, "outside_segments_removed", False))

    def get_post_validation_rebuild(self, obj: VideoFile) -> dict | None:
        return post_validation_rebuild_summary(obj)

    def get_patient_dob(self, obj):
        """
        Returns the patient's date of birth as a date object if available, or None if not present.

        Extracts the date part from the patient's date of birth field in the sensitive metadata, handling both datetime and date types.
        """
        if obj.sensitive_meta and obj.sensitive_meta.patient_dob:
            # If it's a datetime, extract the date part
            dob = obj.sensitive_meta.patient_dob
            return dob.date() if hasattr(dob, "date") else dob
        return None

    def get_examination_date(self, obj):
        """
        Returns the examination date as a date object from the sensitive metadata, or None if unavailable.

        If the examination date is a datetime, only the date part is returned.
        """
        if obj.sensitive_meta and obj.sensitive_meta.examination_date:
            # If it's a datetime, extract the date part
            exam_date = obj.sensitive_meta.examination_date
            return exam_date.date() if hasattr(exam_date, "date") else exam_date
        return None

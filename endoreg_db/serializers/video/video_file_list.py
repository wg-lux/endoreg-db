# endoreg_db/serializers/video/video_file_list.py
from typing import Literal
import logging

from rest_framework import serializers

from endoreg_db.models import VideoFile
from endoreg_db.models.state.frame_annotation import validated_annotators_for_video
from endoreg_db.models.state.video_segment_validation import (
    post_validation_rebuild_summary,
    resolve_segment_annotation_status,
    segment_annotations_are_final,
)
from endoreg_db.serializers.label_video_segment.label_video_segment import (
    LabelVideoSegmentTimelineSerializer,
)

logger = logging.getLogger(__name__)


class VideoFileListSerializer(serializers.ModelSerializer):
    """
    Minimal serializer to return only basic video information
    for the video selection dropdown in Vue.js.

    Convention:
        - Serializer methods must NOT raise if video state is missing or invalid.
        - They return safe defaults and log what went wrong.
    """

    # Add computed fields for video status
    status = serializers.SerializerMethodField()
    assignedUser = serializers.SerializerMethodField()
    anonymized = serializers.SerializerMethodField()
    integrity_status = serializers.SerializerMethodField()
    integrity_error = serializers.SerializerMethodField()
    segment_annotations_validated = serializers.SerializerMethodField()
    segment_annotation_status = serializers.SerializerMethodField()
    outside_segments_removed = serializers.SerializerMethodField()
    post_validation_rebuild = serializers.SerializerMethodField()
    validated_annotators = serializers.SerializerMethodField()
    segments = LabelVideoSegmentTimelineSerializer(
        many=True, read_only=True, source="label_video_segments"
    )

    class Meta:
        model = VideoFile
        fields = [
            "id",
            "original_file_name",
            "status",
            "assignedUser",
            "anonymized",
            "integrity_status",
            "integrity_error",
            "segment_annotations_validated",
            "segment_annotation_status",
            "outside_segments_removed",
            "post_validation_rebuild",
            "validated_annotators",
            "segments",
            "export_segments_by_video",
        ]

    # --- internal helper -------------------------------------------------
    def _get_video_state(self, obj: VideoFile):
        """
        Best-effort accessor for obj.state.

        Serializer layer must never raise here; it only logs and returns None
        if the state cannot be loaded for any reason.
        """
        try:
            return getattr(obj, "state", None)
        except (
            Exception
        ) as exc:  # pragma: no cover - type of error is DB/backend-specific
            logger.warning(
                "VideoFileListSerializer: unable to access state for VideoFile(id=%s): %s",
                getattr(obj, "id", "unknown"),
                exc,
            )
            return None

    # --- public serializer fields ----------------------------------------
    def get_status(
        self, obj: VideoFile
    ) -> Literal["completed", "in_progress", "available", "failed"]:
        """
        Determine the processing status of a video file as 'completed',
        'in_progress', 'failed', or 'available'.

        Contract:
            - Never raises.
            - Missing or invalid state -> treated as 'available'.
        """
        state = self._get_video_state(obj)

        if not state:
            if self.get_integrity_status(obj) == "lost":
                return "failed"
            return "available"

        # Use getattr with defaults to tolerate partially populated state objects
        if getattr(state, "processing_error", False) or self.get_integrity_status(
            obj
        ) == "lost":
            return "failed"
        anonymized = getattr(state, "anonymized", False)
        frames_extracted = getattr(state, "frames_extracted", False)

        if anonymized:
            return "completed"
        if frames_extracted:
            return "in_progress"
        return "available"

    def get_assignedUser(self, obj: VideoFile):
        """
        Returns the user assigned to the video, or None if no user is assigned.

        Currently always returns None as user assignment is not implemented.
        """
        # For now return None, can be extended when user assignment is implemented
        return None

    def get_anonymized(self, obj: VideoFile) -> bool:
        """
        Determine whether the video has been anonymized.

        Contract:
            - Never raises.
            - Returns False if state does not exist or cannot be loaded.
        """
        state = self._get_video_state(obj)
        if not state:
            return False
        if getattr(state, "processing_error", False) or self.get_integrity_status(
            obj
        ) == "lost":
            return False

        # getattr to be robust against partially/populated state
        return bool(getattr(state, "anonymized", False))

    def get_integrity_status(self, obj: VideoFile) -> str:
        payload_obj = getattr(obj, "meta", None)
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        status = str(payload.get("integrity_status") or "").strip()
        if status:
            return status
        state = self._get_video_state(obj)
        if state and getattr(state, "processing_error", False):
            return "lost"
        return ""

    def get_integrity_error(self, obj: VideoFile) -> str:
        payload_obj = getattr(obj, "meta", None)
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        return str(payload.get("integrity_error") or "").strip()

    def get_segment_annotations_validated(self, obj: VideoFile) -> bool:
        """
        Determine whether segment annotations are validated.

        Contract:
            - Never raises.
            - Returns False if state does not exist or cannot be loaded.
        """
        try:
            return bool(segment_annotations_are_final(obj))
        except Exception as exc:
            logger.warning(
                "VideoFileListSerializer: unable to resolve final segment annotation status for VideoFile(id=%s): %s",
                getattr(obj, "id", "unknown"),
                exc,
            )
            return False

    def get_segment_annotation_status(self, obj: VideoFile) -> str:
        try:
            return resolve_segment_annotation_status(obj)
        except Exception as exc:
            logger.warning(
                "VideoFileListSerializer: unable to resolve segment annotation status for VideoFile(id=%s): %s",
                getattr(obj, "id", "unknown"),
                exc,
            )
            return "not_started"

    def get_outside_segments_removed(self, obj: VideoFile) -> bool:
        state = self._get_video_state(obj)
        if not state:
            return False
        return bool(getattr(state, "outside_segments_removed", False))

    def get_post_validation_rebuild(self, obj: VideoFile) -> dict | None:
        try:
            return post_validation_rebuild_summary(obj)
        except Exception as exc:
            logger.warning(
                "VideoFileListSerializer: unable to load post-validation rebuild summary for VideoFile(id=%s): %s",
                getattr(obj, "id", "unknown"),
                exc,
            )
            return None

    def get_validated_annotators(self, obj: VideoFile) -> list[str]:
        """
        Return annotators that already have frame annotations on a validated video.

        This is a dropdown hint for lx-annotate restart workflows: users should
        see when another annotator's validated annotation track already exists.
        """
        if not self.get_segment_annotations_validated(obj):
            return []

        return validated_annotators_for_video(obj)

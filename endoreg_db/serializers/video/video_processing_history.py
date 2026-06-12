"""
Video Processing History Serializer

Serializes VideoProcessingHistory model for API responses.
Created as part of Phase 1.1: Video Correction API Endpoints.
"""

from collections.abc import Callable, Mapping
from typing import Protocol, cast, TYPE_CHECKING

from rest_framework import serializers

if TYPE_CHECKING:
    _ModelSerializerMeta = serializers.ModelSerializer.Meta
else:
    _ModelSerializerMeta = object

from endoreg_db.models.media.video.video_processing import VideoProcessingHistory
from endoreg_db.utils.media_urls import (
    build_absolute_media_url,
    build_video_stream_path,
)


class _VideoLike(Protocol):
    id: int


class _VideoProcessingHistoryLike(Protocol):
    video: _VideoLike
    operation: str
    status: str
    output_file: str


class VideoProcessingHistorySerializer(
    serializers.ModelSerializer[VideoProcessingHistory]
):
    """
    Serializer for VideoProcessingHistory model.

    Provides operation audit trail (masking, frame removal, analysis)
    with download URLs for processed files.
    """

    download_url = serializers.SerializerMethodField()
    operation_display = serializers.SerializerMethodField()
    status_display = serializers.SerializerMethodField()
    duration = serializers.ReadOnlyField()
    is_complete = serializers.ReadOnlyField()

    class Meta(_ModelSerializerMeta):
        model = VideoProcessingHistory  # pyright: ignore[reportAssignmentType]
        fields = [
            "id",
            "video",
            "operation",
            "operation_display",
            "status",
            "status_display",
            "config",
            "output_file",
            "download_url",
            "details",
            "task_id",
            "created_at",
            "completed_at",
            "duration",
            "is_complete",
        ]
        read_only_fields = ["id", "created_at", "completed_at"]

    def get_download_url(self, obj: _VideoProcessingHistoryLike) -> str | None:
        """
        Generate download URL for processed video file.

        Args:
            obj: VideoProcessingHistory instance

        Returns:
            str: URL to download processed file, or None if not available
        """
        if not obj.output_file or obj.status != VideoProcessingHistory.STATUS_SUCCESS:
            return None

        context = cast(Mapping[str, object], self.context)
        request = context.get("request")
        return build_absolute_media_url(
            request,
            build_video_stream_path(obj.video.id, file_type="processed"),
        )

    def get_operation_display(self, obj: _VideoProcessingHistoryLike) -> str:
        display = getattr(obj, "get_operation_display", None)
        if callable(display):
            return str(cast(Callable[[], object], display)())
        return str(obj.operation)

    def get_status_display(self, obj: _VideoProcessingHistoryLike) -> str:
        display = getattr(obj, "get_status_display", None)
        if callable(display):
            return str(cast(Callable[[], object], display)())
        return str(obj.status)

    def validate_operation(self, value: str) -> str:
        """
        Validate operation is one of the defined choices.

        Args:
            value: Operation type

        Returns:
            str: Validated operation

        Raises:
            ValidationError: If operation is invalid
        """
        valid_operations = [
            choice[0] for choice in VideoProcessingHistory.OPERATION_CHOICES
        ]
        if value not in valid_operations:
            raise serializers.ValidationError(
                f"Invalid operation. Must be one of: {', '.join(valid_operations)}"
            )
        return value

    def validate_status(self, value: str) -> str:
        """
        Validate status is one of the defined choices.

        Args:
            value: Status type

        Returns:
            str: Validated status

        Raises:
            ValidationError: If status is invalid
        """
        valid_statuses = [choice[0] for choice in VideoProcessingHistory.STATUS_CHOICES]
        if value not in valid_statuses:
            raise serializers.ValidationError(
                f"Invalid status. Must be one of: {', '.join(valid_statuses)}"
            )
        return value

    def validate_config(self, value: object) -> dict[str, object]:
        """
        Validate config based on operation type.

        Args:
            value: Config dictionary

        Returns:
            dict: Validated config

        Raises:
            ValidationError: If config is invalid for operation
        """
        if not isinstance(value, dict):
            raise serializers.ValidationError("config must be a dictionary")

        config = cast(dict[str, object], value)
        initial_data = cast(
            Mapping[str, object] | None, getattr(self, "initial_data", None)
        )
        operation = (
            cast(str | None, initial_data.get("operation"))
            if initial_data is not None
            else None
        )

        # Validate masking config
        if operation == VideoProcessingHistory.OPERATION_MASKING:
            required_fields = ["mask_type"]
            if "mask_type" not in config:
                raise serializers.ValidationError(
                    f"Masking config must include: {', '.join(required_fields)}"
                )

            # If device mask, require device_name
            if config.get("mask_type") == "device" and "device_name" not in config:
                raise serializers.ValidationError(
                    "Device mask requires 'device_name' in config"
                )

            # If custom ROI, require roi coordinates
            if config.get("mask_type") == "custom" and "roi" not in config:
                raise serializers.ValidationError(
                    "Custom mask requires 'roi' coordinates in config"
                )

        # Validate frame removal config
        elif operation == VideoProcessingHistory.OPERATION_FRAME_REMOVAL:
            if "frame_list" not in config and "detection_method" not in config:
                raise serializers.ValidationError(
                    "Frame removal config must include 'frame_list' (manual) or 'detection_method' (automatic)"
                )

        return config

from rest_framework import serializers
from django.core.exceptions import ObjectDoesNotExist
from django.conf import settings
from urllib.parse import urljoin
from pathlib import Path
from typing import Any, Literal, Dict
import logging
from django.db.models import Prefetch
from django.db import models

from endoreg_db.models import (
    LabelVideoSegment,
    VideoFile,
    Label,
    InformationSource,
    ImageClassificationAnnotation,
)
from endoreg_db.serializers.label_video_segment.image_classification_annotation import (
    ImageClassificationAnnotationSerializer,
)

logger = logging.getLogger(__name__)


# --- Helper Functions ---


def _media_relpath_from_file_path(file_path) -> str:
    """Return a media-relative path (never an absolute server path)."""
    p = Path(str(file_path))
    media_root = getattr(settings, "MEDIA_ROOT", None)
    if media_root:
        try:
            rel = p.resolve().relative_to(Path(media_root).resolve())
            return rel.as_posix()
        except Exception:
            pass
    return p.name  # safe fallback


def _media_url_from_file_path(file_path, request=None) -> str:
    """Build a public URL for the file using MEDIA_URL + relpath."""
    base = getattr(settings, "MEDIA_URL", "/media/")
    if not base.endswith("/"):
        base += "/"
    rel = _media_relpath_from_file_path(file_path)
    url = urljoin(base, rel)
    if request is not None:
        try:
            return request.build_absolute_uri(url)
        except Exception:
            pass
    return url


class LabelVideoSegmentSerializer(serializers.ModelSerializer):
    """Serializer for creating, retrieving, and updating LabelVideoSegment instances."""

    # Write-only fields for Input (Frontend sends seconds)
    start_time = serializers.FloatField(
        write_only=True, required=False, allow_null=True
    )
    end_time = serializers.FloatField(write_only=True, required=False, allow_null=True)

    # Input fields
    video_id = serializers.IntegerField(required=False, help_text="Video file ID")
    label_id = serializers.IntegerField(
        required=False, allow_null=True, help_text="Label ID"
    )
    label_name = serializers.CharField(
        write_only=True, required=False, allow_null=True, help_text="Label name"
    )

    # Read-only fields for Output
    video_name = serializers.SerializerMethodField(read_only=True)
    frame_predictions = serializers.SerializerMethodField(read_only=True)
    manual_frame_annotations = serializers.SerializerMethodField(read_only=True)
    time_segments = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = LabelVideoSegment
        fields = [
            "id",
            "video_file",
            "video_name",
            "video_id",
            "label",
            "label_name",
            "label_id",
            "start_frame_number",
            "end_frame_number",
            "start_time",
            "end_time",
            "frame_predictions",
            "manual_frame_annotations",
            "time_segments",
        ]
        read_only_fields = ["id", "video_name"]
        extra_kwargs = {
            "start_frame_number": {"required": False},
            "end_frame_number": {"required": False},
            "video_file": {"required": False},
            "label": {"required": False},
        }

    # --- Internal Helpers ---

    def _get_video_file(self, video_id) -> VideoFile:
        try:
            return VideoFile.objects.get(id=video_id)
        except ObjectDoesNotExist:
            raise serializers.ValidationError(
                f"VideoFile with id {video_id} does not exist"
            )

    def _get_label(self, label_id, label_name):
        if label_id:
            try:
                return Label.objects.get(id=label_id)
            except ObjectDoesNotExist:
                raise serializers.ValidationError(
                    f"Label with id {label_id} does not exist"
                )
        elif label_name:
            label, _ = Label.get_or_create_from_name(label_name)
            if not label:
                raise serializers.ValidationError(
                    f"Failed to create or retrieve label with name {label_name}"
                )
            return label
        return None

    def _validate_fps(self, video_file) -> float:
        """Helper to get valid FPS from video file."""
        fps = video_file.get_fps()
        if not fps or fps <= 0:
            raise serializers.ValidationError(
                "Video file must have a defined, positive FPS to calculate frames."
            )
        return float(fps)

    def _convert_time_to_frame(self, time_val, fps):
        return int(round(float(time_val) * fps))

    def _get_information_source(self) -> InformationSource:
        source, _ = InformationSource.objects.get_or_create(
            name="Manual Annotation",
            defaults={
                "description": "Manually created label segments via web interface"
            },
        )
        return source

    # --- DRF Overrides ---

    def to_internal_value(self, data) -> Any:
        """Normalize input data keys."""
        # Frontend might send video_file or video_id, label or label_id
        if "video_file" in data:
            data["video_id"] = data["video_file"]
        if "label" in data:
            data["label_id"] = data["label"]
        return super().to_internal_value(data)

    def validate(self, attrs) -> Any:
        """
        Validate logical consistency:
        1. Ensure we have Video reference.
        2. Ensure we have EITHER (start_time, end_time) OR (start_frame, end_frame).
        3. Ensure Start < End.
        """
        # 1. Video Check
        video_id = attrs.get("video_id") or self.initial_data.get("video_id")
        if not video_id and not self.instance:
            raise serializers.ValidationError("video_id is required.")

        # 2. Time vs Frame Check
        start_time = attrs.get("start_time")
        end_time = attrs.get("end_time")
        start_frame = attrs.get("start_frame_number")
        end_frame = attrs.get("end_frame_number")

        # If updating, fallback to instance values
        if self.instance:
            if start_time is None and "start_time" not in attrs:
                # We don't have time in attrs, but we might have frames
                pass
            if start_frame is None:
                start_frame = self.instance.start_frame_number
            if end_frame is None:
                end_frame = self.instance.end_frame_number

        has_time = start_time is not None and end_time is not None
        has_frames = start_frame is not None and end_frame is not None

        if not has_time and not has_frames:
            # If creating, strictly require one set
            if not self.instance:
                raise serializers.ValidationError(
                    "Either (start_time, end_time) OR (start_frame_number, end_frame_number) must be provided."
                )

        # 3. Logical Constraints
        if has_time:
            if start_time < 0:
                raise serializers.ValidationError(
                    {"start_time": "Must be non-negative."}
                )
            if end_time <= start_time:
                raise serializers.ValidationError(
                    {"end_time": "Must be greater than start_time."}
                )

        if has_frames:
            if start_frame < 0:
                raise serializers.ValidationError(
                    {"start_frame_number": "Must be non-negative."}
                )
            if end_frame <= start_frame:
                raise serializers.ValidationError(
                    {"end_frame_number": "Must be greater than start_frame_number."}
                )

        return attrs

    def create(self, validated_data) -> LabelVideoSegment:
        """
        Create logic:
        1. Extract ID/Name/Time data.
        2. Resolve Objects (Video, Label).
        3. Convert Time -> Frames.
        4. Save.
        """
        try:
            # Extract basic data
            video_id = validated_data.pop("video_id")
            label_id = validated_data.pop("label_id", None)
            label_name = validated_data.pop("label_name", None)

            # Extract time data (might be None if frames were passed directly)
            start_time = validated_data.pop("start_time", None)
            end_time = validated_data.pop("end_time", None)

            # Resolve Objects
            video_file = self._get_video_file(video_id)
            label = self._get_label(label_id, label_name)
            source = self._get_information_source()

            # Calculate Frames if time is provided
            if start_time is not None and end_time is not None:
                fps = self._validate_fps(video_file)
                validated_data["start_frame_number"] = self._convert_time_to_frame(
                    start_time, fps
                )
                validated_data["end_frame_number"] = self._convert_time_to_frame(
                    end_time, fps
                )

            # Final check to ensure we have frames (in case validation slipped or logic failed)
            if (
                "start_frame_number" not in validated_data
                or "end_frame_number" not in validated_data
            ):
                raise serializers.ValidationError(
                    "Could not determine frame numbers. Please provide start_time/end_time."
                )

            # Create
            segment = LabelVideoSegment.safe_create(
                video_file=video_file,
                label=label,
                source=source,
                start_frame_number=validated_data["start_frame_number"],
                end_frame_number=validated_data["end_frame_number"],
                prediction_meta=None,
            )
            segment.save()

            logger.info(f"Created segment {segment.pk} for video {video_id}")
            return segment

        except Exception as e:
            logger.error(f"Error creating segment: {e}")
            raise serializers.ValidationError(str(e))

    def update(self, instance, validated_data) -> Any:
        """
        Update logic:
        1. Check if Video changed (affects FPS).
        2. Check if Label changed.
        3. Check if Time changed -> Recalculate Frames.
        """
        try:
            # Pop fields
            video_id = validated_data.pop("video_id", None)
            label_id = validated_data.pop("label_id", None)
            label_id_present = "label_id" in validated_data
            label_name_present = "label_name" in validated_data
            label_name = validated_data.pop("label_name", None)
            start_time = validated_data.pop("start_time", None)
            end_time = validated_data.pop("end_time", None)

            # 1. Update Video?
            current_video = instance.video_file
            if video_id and current_video.id != video_id:
                current_video = self._get_video_file(video_id)
                instance.video_file = current_video

            # 2. Update Label?
            if label_id_present or label_name_present:
                if label_id or label_name:
                    instance.label = self._get_label(label_id, label_name)
                else:
                    instance.label = None

            # 3. Update Frames (from Time or direct Frames)
            # We need FPS if we are using time
            fps = None
            if start_time is not None or end_time is not None:
                fps = self._validate_fps(current_video)

            if start_time is not None:
                instance.start_frame_number = self._convert_time_to_frame(
                    start_time, fps
                )
            elif "start_frame_number" in validated_data:
                instance.start_frame_number = validated_data["start_frame_number"]

            if end_time is not None:
                instance.end_frame_number = self._convert_time_to_frame(end_time, fps)
            elif "end_frame_number" in validated_data:
                instance.end_frame_number = validated_data["end_frame_number"]

            # Final Frame Safety Check
            if instance.start_frame_number >= instance.end_frame_number:
                raise serializers.ValidationError(
                    "start_time/frame must be strictly less than end_time/frame"
                )

            instance.save()
            logger.info(f"Updated segment {instance.pk}")
            return instance

        except Exception as e:
            logger.error(f"Error updating segment {instance.pk}: {e}")
            raise serializers.ValidationError(str(e))

    # --- Read/Representation Methods (Already existed) ---

    def to_representation(self, instance) -> dict[str, Any]:
        """Inject calculated seconds and IDs for frontend convenience."""
        data = super().to_representation(instance)

        video = instance.video_file
        if video:
            data["start_time"] = video.frame_number_to_s(instance.start_frame_number)
            data["end_time"] = video.frame_number_to_s(instance.end_frame_number)
            data["video_id"] = video.id

        if instance.label:
            data["label_name"] = instance.label.name
            data["label_id"] = instance.label.id
        else:
            data["label_name"] = None
            data["label_id"] = None

        return data

    def get_time_segments(self, obj: LabelVideoSegment) -> dict[str, dict]:
        annotations_prefetch = Prefetch(
            "image_classification_annotations",
            queryset=ImageClassificationAnnotation.objects.select_related("label"),
        )
        assert isinstance(obj, LabelVideoSegment)
        assert isinstance(obj.frames, models.QuerySet)
        frames = obj.frames.prefetch_related(annotations_prefetch)
        time_segments = {
            "segment_id": obj.pk,
            "segment_start": obj.start_frame_number,
            "segment_end": obj.end_frame_number,
            "start_time": obj.start_time,
            "end_time": obj.end_time,
            "frames": [],
        }

        request = self.context.get("request") if hasattr(self, "context") else None

        for frame in frames:
            # Optimization: Use annotations if available to avoid N+1 queries
            all_classifications = ImageClassificationAnnotationSerializer(
                frame.image_classification_annotations.all(), many=True
            ).data

            # Use safe helpers for paths
            rel = _media_relpath_from_file_path(frame.file_path)
            url = _media_url_from_file_path(frame.file_path, request=request)

            frame_data = {
                "frame_filename": Path(str(frame.file_path)).name,
                "frame_file_path": rel,
                "frame_url": url,
                "all_classifications": all_classifications,
                "frame_id": frame.pk,
            }
            time_segments["frames"].append(frame_data)

        return time_segments

    def get_label_name(self, obj) -> Any | Literal["Unknown"]:
        return obj.label.name if obj.label else "Unknown"

    def get_manual_frame_annotations(self, obj: LabelVideoSegment) -> Dict[Any, Any]:
        return ImageClassificationAnnotationSerializer(
            obj.manual_frame_annotations, many=True
        ).data

    def get_frame_predictions(self, obj: LabelVideoSegment) -> Dict[Any, Any]:
        return ImageClassificationAnnotationSerializer(
            obj.frame_predictions, many=True
        ).data

    def get_video_name(self, obj) -> Any | str:
        try:
            video = obj.video_file
            return getattr(video, "original_file_name", f"Video {video.id}")
        except (AttributeError, ObjectDoesNotExist):
            return "Unknown Video"

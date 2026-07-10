from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol, cast

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Prefetch
from pydantic import ValidationError as PydanticValidationError
from rest_framework import serializers

from endoreg_db.models.label.annotation.image_classification import (
    ImageClassificationAnnotation,
)
from endoreg_db.models.label.label import Label
from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.models.media.frame.frame import Frame
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.other.information_source import InformationSource
from endoreg_db.serializers.label_video_segment.image_classification_annotation import (
    ImageClassificationAnnotationSerializer,
)
from endoreg_db.services.video_files import get_video_fps, video_frame_number_to_seconds
from endoreg_db.utils.media_urls import (
    build_absolute_media_url,
    build_video_frame_stream_path,
)
from lx_dtypes.models.contracts.label_video_segment_serializer import (
    LabelVideoSegmentFrameClassificationPayload,
    LabelVideoSegmentTimeSegmentPayload,
)
from lx_dtypes.models.contracts.video_segments import SegmentAnnotationInput

logger = logging.getLogger(__name__)


class RequestLike(Protocol):
    def get(self, key: str, default: object = ...) -> object: ...


class ContextLike(Protocol):
    def get(self, key: str, default: object = ...) -> object: ...


class LabelLike(Protocol):
    id: int
    name: str


class SourceLike(Protocol):
    name: str


class VideoLike(Protocol):
    id: int

    @property
    def original_file_name(self) -> str | None: ...

    def frame_number_to_s(self, frame_number: int) -> float: ...


class LabelFileLike(Protocol):
    id: int
    name: str


class _SerializerDataLike(Protocol):
    @property
    def data(self) -> Sequence[Mapping[str, object]]: ...


def _serializer_rows(serializer: object) -> list[dict[str, object]]:
    return [dict(item) for item in cast(_SerializerDataLike, serializer).data]


def _segment_frame_number(segment: object, field_name: str) -> int:
    value = getattr(segment, field_name, None)
    if not isinstance(value, int):
        raise serializers.ValidationError(f"{field_name} must be an integer.")
    return value


class FrameLike(Protocol):
    pk: int
    video_id: int
    frame_number: int
    file_path: Path
    image_classification_annotations: Sequence[ImageClassificationAnnotation]


class LabelVideoSegmentLike(Protocol):
    pk: int
    label: LabelLike | None
    source: SourceLike | None
    prediction_meta_id: int | None
    video_file: object
    start_frame_number: int
    end_frame_number: int
    start_time: float | None
    end_time: float | None
    export_segment: bool
    frames: Sequence[FrameLike]
    manual_frame_annotations: Sequence[ImageClassificationAnnotation]
    frame_predictions: Sequence[ImageClassificationAnnotation]
    video_file_id: int

    def save(self) -> None: ...


class LabelVideoSegmentTimelineSerializer(
    serializers.ModelSerializer[LabelVideoSegment]
):
    label_id = serializers.SerializerMethodField()
    label_name = serializers.SerializerMethodField()
    source_name = serializers.SerializerMethodField()
    segment_origin = serializers.SerializerMethodField()
    prediction_meta_id = serializers.SerializerMethodField()
    start_time = serializers.SerializerMethodField()
    end_time = serializers.SerializerMethodField()

    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        model = LabelVideoSegment  # pyright: ignore[reportAssignmentType]
        fields = [
            "id",
            "label_id",
            "label_name",
            "source_name",
            "segment_origin",
            "prediction_meta_id",
            "start_frame_number",
            "end_frame_number",
            "start_time",
            "end_time",
            "export_segment",
        ]

    def _frame_to_seconds(
        self, obj: LabelVideoSegmentLike, frame_number: int
    ) -> float | None:
        video = cast(VideoLike | None, obj.video_file)
        if video is None:
            return None
        try:
            return video_frame_number_to_seconds(cast(VideoFile, video), frame_number)
        except Exception:
            return None

    def get_label_id(self, obj: LabelVideoSegmentLike) -> int | None:
        label = cast(LabelFileLike | None, obj.label)
        return None if label is None else label.id

    def get_label_name(self, obj: LabelVideoSegmentLike) -> str | None:
        label = cast(LabelFileLike | None, obj.label)
        return None if label is None else label.name

    def get_source_name(self, obj: LabelVideoSegmentLike) -> str | None:
        return None if obj.source is None else obj.source.name

    def get_segment_origin(self, obj: LabelVideoSegmentLike) -> str:
        source_name = self.get_source_name(obj)
        if obj.prediction_meta_id is not None or source_name == "prediction":
            return "prediction"
        return "manual"

    def get_prediction_meta_id(self, obj: LabelVideoSegmentLike) -> int | None:
        return obj.prediction_meta_id

    def get_start_time(self, obj: LabelVideoSegmentLike) -> float | None:
        return self._frame_to_seconds(obj, obj.start_frame_number)

    def get_end_time(self, obj: LabelVideoSegmentLike) -> float | None:
        return self._frame_to_seconds(obj, obj.end_frame_number)


class LabelVideoSegmentSerializer(serializers.ModelSerializer[LabelVideoSegment]):
    start_time = serializers.FloatField(
        write_only=True, required=False, allow_null=True
    )
    end_time = serializers.FloatField(write_only=True, required=False, allow_null=True)
    video_id = serializers.IntegerField(required=False, help_text="Video file ID")
    label_id = serializers.IntegerField(
        required=False, allow_null=True, help_text="Label ID"
    )
    label_name = serializers.CharField(
        write_only=True, required=False, allow_null=True, help_text="Label name"
    )
    video_name = serializers.SerializerMethodField(read_only=True)
    source_name = serializers.SerializerMethodField(read_only=True)
    segment_origin = serializers.SerializerMethodField(read_only=True)
    prediction_meta_id = serializers.IntegerField(read_only=True)
    frame_predictions = serializers.SerializerMethodField(read_only=True)
    manual_frame_annotations = serializers.SerializerMethodField(read_only=True)
    time_segments = serializers.SerializerMethodField(read_only=True)

    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        model = LabelVideoSegment  # pyright: ignore[reportAssignmentType]
        fields = [
            "id",
            "video_file",
            "video_name",
            "source_name",
            "segment_origin",
            "prediction_meta_id",
            "video_id",
            "label",
            "label_name",
            "label_id",
            "start_frame_number",
            "end_frame_number",
            "start_time",
            "end_time",
            "export_segment",
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
            "export_segment": {"required": False},
        }

    def _include_annotation_payload(self) -> bool:
        context = cast(ContextLike, getattr(self, "context", {}))
        return bool(context.get("include_annotation_payload", True))

    def _get_video_file(self, video_id: int) -> VideoFile:
        context = cast(ContextLike, getattr(self, "context", {}))
        context_video = context.get("video_file")
        if isinstance(context_video, VideoFile) and context_video.pk == video_id:
            return context_video

        try:
            return VideoFile.objects.get(id=video_id)
        except ObjectDoesNotExist as exc:
            raise serializers.ValidationError(
                f"VideoFile with id {video_id} does not exist"
            ) from exc

    def _get_label(self, label_id: int | None, label_name: str | None) -> Label | None:
        if label_id is not None:
            try:
                return Label.objects.get(id=label_id)
            except ObjectDoesNotExist as exc:
                raise serializers.ValidationError(
                    f"Label with id {label_id} does not exist"
                ) from exc
        if label_name is not None:
            label, _ = Label.get_or_create_from_name(label_name)
            return label
        return None

    def _validate_fps(self, video_file: object) -> float:
        context = cast(ContextLike, getattr(self, "context", {}))
        context_video_id = context.get("video_id")
        context_fps = context.get("video_fps")
        if (
            isinstance(video_file, VideoFile)
            and isinstance(context_video_id, int)
            and video_file.pk == context_video_id
            and isinstance(context_fps, (int, float))
            and not isinstance(context_fps, bool)
            and context_fps > 0
        ):
            return float(context_fps)

        fps = get_video_fps(cast(VideoFile, video_file))
        if not fps or fps <= 0:
            raise serializers.ValidationError(
                "Video file must have a defined, positive FPS to calculate frames."
            )
        return float(fps)

    def _convert_time_to_frame(self, time_val: float, fps: float) -> int:
        return int(round(time_val * fps))

    def _get_information_source(self) -> InformationSource:
        source_name = "manual_annotation"
        sources = list(
            InformationSource.objects.filter(name=source_name).order_by("id")[:2]
        )
        if sources:
            if len(sources) > 1:
                logger.warning(
                    "Multiple InformationSource rows found for name '%s'; using first.",
                    source_name,
                )
            return sources[0]
        return InformationSource.objects.create(
            name=source_name,
            description="Manually created label segments via web interface",
        )

    def _validate_segment_contract(
        self, video_id: int, start_time: float, end_time: float
    ) -> None:
        if end_time <= start_time:
            raise serializers.ValidationError(
                {"end_time": "end_time must be greater than start_time."}
            )
        try:
            SegmentAnnotationInput.model_validate(
                {
                    "type": "segment",
                    "video_id": video_id,
                    "start_time": start_time,
                    "end_time": end_time,
                    "metadata": {},
                }
            )
        except PydanticValidationError as exc:
            errors: dict[str, list[str]] = {}
            for error in exc.errors():
                location = error.get("loc", ())
                message = str(error.get("msg", "Invalid value"))
                if not location and "end_time" in message:
                    field = "end_time"
                else:
                    field = (
                        str(location[-1])
                        if location
                        and str(location[-1]) in {"video_id", "start_time", "end_time"}
                        else "non_field_errors"
                    )
                errors.setdefault(field, []).append(message)
            raise serializers.ValidationError(errors) from exc

    def to_internal_value(self, data: Mapping[str, object]) -> dict[str, object]:
        payload = dict(data)
        if "video_file" in payload:
            payload["video_id"] = payload["video_file"]
        if "label" in payload:
            payload["label_id"] = payload["label"]
        return super().to_internal_value(payload)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        start_time = attrs.get("start_time")
        end_time = attrs.get("end_time")
        start_frame = attrs.get("start_frame_number")
        end_frame = attrs.get("end_frame_number")
        effective_start_time = start_time
        effective_end_time = end_time

        if self.instance is not None:
            instance = cast(LabelVideoSegmentLike, self.instance)
            if start_time is None and "start_time" not in attrs:
                effective_start_time = instance.start_time
            if end_time is None and "end_time" not in attrs:
                effective_end_time = instance.end_time
            if start_frame is None:
                start_frame = instance.start_frame_number
            if end_frame is None:
                end_frame = instance.end_frame_number

        has_time = start_time is not None and end_time is not None
        has_frames = start_frame is not None and end_frame is not None
        has_effective_time = (
            effective_start_time is not None and effective_end_time is not None
        )

        if not has_time and not has_frames and self.instance is None:
            raise serializers.ValidationError(
                "Either (start_time, end_time) OR (start_frame_number, end_frame_number) must be provided."
            )

        if has_effective_time:
            effective_video_id = attrs.get("video_id")
            if self.instance is not None and effective_video_id is None:
                effective_video_id = cast(
                    LabelVideoSegmentLike, self.instance
                ).video_file_id
            if effective_video_id is None:
                raise serializers.ValidationError(
                    {"video_id": "This field is required."}
                )
            self._validate_segment_contract(
                int(cast(int, effective_video_id)),
                float(cast(float, effective_start_time)),
                float(cast(float, effective_end_time)),
            )

        if has_frames and isinstance(start_frame, int) and isinstance(end_frame, int):
            if start_frame < 0:
                raise serializers.ValidationError(
                    {"start_frame_number": "Must be non-negative."}
                )
            if end_frame <= start_frame:
                raise serializers.ValidationError(
                    {"end_frame_number": "Must be greater than start_frame_number."}
                )

        video_id = attrs.get("video_id") or self.initial_data.get("video_id")
        if not video_id and self.instance is None:
            raise serializers.ValidationError("video_id is required.")
        return attrs

    def create(self, validated_data: dict[str, object]) -> LabelVideoSegment:
        try:
            video_id = int(cast(int, validated_data.pop("video_id")))
            label_id = cast(int | None, validated_data.pop("label_id", None))
            label_name = cast(str | None, validated_data.pop("label_name", None))
            export_segment = bool(validated_data.pop("export_segment", False))
            start_time = cast(float | None, validated_data.pop("start_time", None))
            end_time = cast(float | None, validated_data.pop("end_time", None))

            video_file = self._get_video_file(video_id)
            label = self._get_label(label_id, label_name)
            source = self._get_information_source()

            if start_time is not None and end_time is not None:
                fps = self._validate_fps(video_file)
                validated_data["start_frame_number"] = self._convert_time_to_frame(
                    start_time, fps
                )
                validated_data["end_frame_number"] = self._convert_time_to_frame(
                    end_time, fps
                )

            if (
                "start_frame_number" not in validated_data
                or "end_frame_number" not in validated_data
            ):
                raise serializers.ValidationError(
                    "Could not determine frame numbers. Please provide start_time/end_time."
                )

            segment = LabelVideoSegment.safe_create(
                video_file=video_file,
                label=label,
                source=source,
                start_frame_number=int(cast(int, validated_data["start_frame_number"])),
                end_frame_number=int(cast(int, validated_data["end_frame_number"])),
                prediction_meta=None,
                export_segment=export_segment,
            )  # this function handles segment.save()
            return segment
        except Exception as exc:
            logger.error("Error creating segment: %s", exc)
            raise serializers.ValidationError(str(exc)) from exc

    def update(
        self, instance: LabelVideoSegment, validated_data: dict[str, object]
    ) -> LabelVideoSegment:
        try:
            label_id_present = "label_id" in validated_data
            label_name_present = "label_name" in validated_data
            video_id = cast(int | None, validated_data.pop("video_id", None))
            label_id = cast(int | None, validated_data.pop("label_id", None))
            label_name = cast(str | None, validated_data.pop("label_name", None))
            start_time = cast(float | None, validated_data.pop("start_time", None))
            end_time = cast(float | None, validated_data.pop("end_time", None))
            export_segment = validated_data.pop("export_segment", None)

            current_video = cast(VideoLike | None, instance.video_file)
            if (
                video_id is not None
                and current_video is not None
                and cast(int, getattr(current_video, "id")) != video_id
            ):
                current_video = self._get_video_file(video_id)
                instance.video_file = current_video

            if label_id_present or label_name_present:
                instance.label = self._get_label(label_id, label_name)

            fps: float | None = None
            if start_time is not None or end_time is not None:
                if current_video is None:
                    raise serializers.ValidationError(
                        {"video_id": "This field is required."}
                    )
                fps = self._validate_fps(current_video)

            if start_time is not None:
                instance.start_frame_number = self._convert_time_to_frame(
                    start_time, cast(float, fps)
                )
            elif "start_frame_number" in validated_data:
                instance.start_frame_number = int(
                    cast(int, validated_data["start_frame_number"])
                )

            if end_time is not None:
                instance.end_frame_number = self._convert_time_to_frame(
                    end_time, cast(float, fps)
                )
            elif "end_frame_number" in validated_data:
                instance.end_frame_number = int(
                    cast(int, validated_data["end_frame_number"])
                )

            if export_segment is not None:
                instance.export_segment = bool(export_segment)

            start_frame_number = _segment_frame_number(instance, "start_frame_number")
            end_frame_number = _segment_frame_number(instance, "end_frame_number")
            if start_frame_number >= end_frame_number:
                raise serializers.ValidationError(
                    "start_time/frame must be strictly less than end_time/frame"
                )

            instance.save()
            return instance
        except Exception as exc:
            logger.error("Error updating segment %s: %s", instance.pk, exc)
            raise serializers.ValidationError(str(exc)) from exc

    def to_representation(self, instance: LabelVideoSegment) -> dict[str, object]:
        data = super().to_representation(instance)
        video = cast(VideoLike | None, instance.video_file)
        if video is not None:
            start_frame_number = _segment_frame_number(instance, "start_frame_number")
            end_frame_number = _segment_frame_number(instance, "end_frame_number")
            data["start_time"] = video.frame_number_to_s(start_frame_number)
            data["end_time"] = video.frame_number_to_s(end_frame_number)
            data["video_id"] = cast(int, getattr(instance.video_file, "id"))
        label = cast(LabelFileLike | None, instance.label)
        if label is not None:
            data["label_name"] = label.name
            data["label_id"] = label.id
        else:
            data["label_name"] = None
            data["label_id"] = None
        return data

    def get_source_name(self, obj: LabelVideoSegmentLike) -> str | None:
        return None if obj.source is None else obj.source.name

    def get_segment_origin(self, obj: LabelVideoSegmentLike) -> str:
        source_name = self.get_source_name(obj)
        if obj.prediction_meta_id is not None or source_name == "prediction":
            return "prediction"
        return "manual"

    def get_prediction_meta_id(self, obj: LabelVideoSegmentLike) -> int | None:
        return obj.prediction_meta_id

    def _segment_frames(self, obj: LabelVideoSegmentLike) -> list[FrameLike]:
        queryset = (
            Frame.objects.filter(
                video_id=obj.video_file_id,
                frame_number__gte=obj.start_frame_number,
                frame_number__lt=obj.end_frame_number,
            )
            .select_related("video")
            .order_by("frame_number")
            .prefetch_related(
                Prefetch(
                    "image_classification_annotations",
                    queryset=ImageClassificationAnnotation.objects.select_related(
                        "label",
                        "information_source",
                    ),
                )
            )
        )
        return [cast(FrameLike, frame) for frame in queryset]

    def get_time_segments(self, obj: LabelVideoSegmentLike) -> dict[str, object]:
        if not self._include_annotation_payload():
            start_time = None
            end_time = None
            video = cast(VideoLike | None, obj.video_file)
            if video is not None:
                try:
                    start_time = video.frame_number_to_s(obj.start_frame_number)
                    end_time = video.frame_number_to_s(obj.end_frame_number)
                except Exception:
                    start_time = None
                    end_time = None
            return {
                "segment_id": obj.pk,
                "segment_start": obj.start_frame_number,
                "segment_end": obj.end_frame_number,
                "start_time": start_time,
                "end_time": end_time,
                "frames": [],
            }

        frames = self._segment_frames(obj)
        frames_payload: list[LabelVideoSegmentFrameClassificationPayload] = []

        request = cast(
            RequestLike | None,
            self.context.get("request") if hasattr(self, "context") else None,
        )

        for frame in frames:
            all_classifications = _serializer_rows(
                ImageClassificationAnnotationSerializer(
                    frame.image_classification_annotations, many=True
                )
            )
            url = build_absolute_media_url(
                request,
                build_video_frame_stream_path(frame.video_id, frame.frame_number),
            )
            frames_payload.append(
                LabelVideoSegmentFrameClassificationPayload(
                    frame_filename=Path(str(frame.file_path)).name,
                    frame_file_path=Path(str(frame.file_path)).name,
                    frame_url=url,
                    all_classifications=all_classifications,
                    frame_id=frame.pk,
                )
            )

        time_segments = LabelVideoSegmentTimeSegmentPayload(
            segment_id=obj.pk,
            segment_start=obj.start_frame_number,
            segment_end=obj.end_frame_number,
            start_time=obj.start_time,
            end_time=obj.end_time,
            frames=frames_payload,
        )
        return time_segments.model_dump(mode="python")

    def get_label_name(self, obj: LabelVideoSegmentLike) -> str:
        label = cast(LabelFileLike | None, obj.label)
        return label.name if label is not None else "Unknown"

    def get_manual_frame_annotations(
        self, obj: LabelVideoSegmentLike
    ) -> list[dict[str, object]]:
        if not self._include_annotation_payload():
            return []
        return _serializer_rows(
            ImageClassificationAnnotationSerializer(
                obj.manual_frame_annotations, many=True
            )
        )

    def get_frame_predictions(
        self, obj: LabelVideoSegmentLike
    ) -> list[dict[str, object]]:
        if not self._include_annotation_payload():
            return []
        return _serializer_rows(
            ImageClassificationAnnotationSerializer(obj.frame_predictions, many=True)
        )

    def get_video_name(self, obj: LabelVideoSegmentLike) -> str:
        try:
            video = cast(VideoLike | None, obj.video_file)
            if video is None:
                return "Unknown Video"
            original_file_name = cast(
                str | None, getattr(obj.video_file, "original_file_name", None)
            )
            if original_file_name is not None:
                return original_file_name
            return f"Video {cast(int, getattr(obj.video_file, 'id'))}"
        except (AttributeError, ObjectDoesNotExist):
            return "Unknown Video"

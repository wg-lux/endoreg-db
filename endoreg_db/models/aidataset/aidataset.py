from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from django.db import models
from pydantic import BaseModel, Field
from lx_dtypes.models.ledger.p_video.Pydantic import PatientVideoFile

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from endoreg_db.models import (
        ImageClassificationAnnotation,
        Label,
        LabelVideoSegment,
        VideoFile,
    )


class AIDataSetFrameLabelExport(BaseModel):
    id: int
    name: str
    labelset_name: str | None = None


class AIDataSetFrameAnnotationExport(BaseModel):
    annotation_id: int
    frame_id: int
    frame_number: int
    timestamp: float | None = None
    relative_path: str
    file_path: str | None = None
    patient_video_file_uuid: str
    video_id: int
    video_uuid: str
    video_hash: str
    original_file_name: str | None = None
    label: AIDataSetFrameLabelExport
    value: bool
    confidence: float | None = None
    annotator: str | None = None
    information_source_name: str | None = None
    model_meta_id: int | None = None
    external_annotation_id: str | None = None
    date_created: datetime
    date_modified: datetime


class AIDataSetExportSummary(BaseModel):
    image_annotation_count: int = 0
    video_annotation_count: int = 0
    frame_count: int = 0
    video_count: int = 0
    label_count: int = 0


class AIDataSetExportPayload(BaseModel):
    schema_version: str = "1.0"
    dataset_id: int
    name: str | None = None
    description: str | None = None
    dataset_type: str
    ai_model_type: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    summary: AIDataSetExportSummary
    patient_videos: dict[str, "PatientVideoFile"] = Field(default_factory=dict)
    frame_annotations: list[AIDataSetFrameAnnotationExport] = Field(
        default_factory=list
    )


AIDataSetExportPayload.model_rebuild()


class AIDataSet(models.Model):
    """
    Aggregates persisted frame annotations and video-segment annotations for AI usage.

    Frame annotations come from the frame annotation workflow and are stored as
    `ImageClassificationAnnotation`. Timeline/video annotations come from the
    video examination workflow and are stored as `LabelVideoSegment`.

    The export helpers return a validated pydantic payload so downstream code can
    consume a single standardized structure.
    """

    DATASET_TYPE_IMAGE = "image"
    DATASET_TYPE_VIDEO = "video"
    DATASET_TYPE_TEXT = "text"

    DATASET_TYPE_CHOICES = [
        (DATASET_TYPE_IMAGE, "Image"),
        (DATASET_TYPE_VIDEO, "Video"),
    ]

    AI_MODEL_TYPE_IMAGE_MULTILABEL = "image_multilabel_classification"
    AI_MODEL_TYPE_VIDEO_SEGMENT_CLASSIFICATION = "video_segment_classification"

    name = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text='Human-readable identifier, e.g. "Legacy multilabel dataset v1".',
    )
    description = models.TextField(
        blank=True,
        null=True,
        help_text="Optional notes / explanation about this dataset.",
    )
    ai_model_type = models.CharField(
        max_length=255,
        default=AI_MODEL_TYPE_IMAGE_MULTILABEL,
        help_text=(
            "AI model family this dataset is for, e.g. "
            '"image_multilabel_classification".'
        ),
    )
    dataset_type = models.CharField(
        max_length=32,
        choices=DATASET_TYPE_CHOICES,
        default=DATASET_TYPE_IMAGE,
        help_text=(
            "Primary annotation modality used for training. Export helpers may "
            "still include both frame and video annotations attached to the dataset."
        ),
    )
    image_annotations = models.ManyToManyField(
        "ImageClassificationAnnotation",
        related_name="image_ai_datasets",
        blank=True,
        help_text=(
            "Frame-level annotations collected from the frame annotation workflow."
        ),
    )
    video_annotations = models.ManyToManyField(
        "LabelVideoSegment",
        related_name="video_ai_datasets",
        blank=True,
        help_text=(
            "Video-segment annotations collected from the video examination "
            "annotation workflow."
        ),
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When this AIDataSet was created.",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="When this AIDataSet was last modified.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Soft toggle to enable/disable this dataset for training.",
    )

    if TYPE_CHECKING:
        image_annotations: models.Manager[ImageClassificationAnnotation]
        video_annotations: models.Manager[LabelVideoSegment]

    @staticmethod
    def _resolve_labelset_name(label: Label | None) -> str | None:
        if label is None:
            return None
        labelset = label.label_sets.order_by("-version", "name").first()
        if labelset is None:
            return None
        return labelset.name

    @staticmethod
    def _coerce_objects(
        objects: Iterable[models.Model] | QuerySet[models.Model],
    ) -> list[models.Model]:
        if isinstance(objects, models.QuerySet):
            return list(objects)
        return list(objects)

    def get_image_annotations_queryset(
        self,
    ) -> models.Manager[ImageClassificationAnnotation]:
        return self.image_annotations

    def get_video_annotations_queryset(self) -> models.Manager[LabelVideoSegment]:
        return self.video_annotations

    def get_annotations_queryset(self):
        """
        Backwards-compatible helper used by existing training code.
        """
        if self.dataset_type == self.DATASET_TYPE_IMAGE:
            return self.image_annotations
        if self.dataset_type == self.DATASET_TYPE_VIDEO:
            return self.video_annotations
        return self.image_annotations.none()

    def add_frame_annotations(
        self,
        annotations: Iterable[ImageClassificationAnnotation]
        | QuerySet[ImageClassificationAnnotation],
    ) -> int:
        annotation_list = self._coerce_objects(annotations)
        if not annotation_list:
            return 0
        self.image_annotations.add(*annotation_list)
        return len(annotation_list)

    def add_video_annotations(
        self,
        segments: Iterable[LabelVideoSegment] | QuerySet[LabelVideoSegment],
    ) -> int:
        segment_list = self._coerce_objects(segments)
        if not segment_list:
            return 0
        self.video_annotations.add(*segment_list)
        return len(segment_list)

    def attach_video(
        self,
        video: VideoFile | int,
        *,
        include_frame_annotations: bool = True,
        include_video_annotations: bool = True,
        information_source_names: Iterable[str] | None = None,
    ) -> dict[str, int]:
        """
        Attach already-persisted annotations for a video to this dataset.

        This is the bridge from the existing frontend persistence flow to the dataset:
        the Vue screens write to `ImageClassificationAnnotation` and `LabelVideoSegment`,
        and the dataset aggregates those rows without duplicating annotation storage.
        """
        from endoreg_db.models import (
            ImageClassificationAnnotation,
            LabelVideoSegment,
            VideoFile,
        )

        if isinstance(video, int):
            video = VideoFile.objects.get(pk=video)

        normalized_source_names = None
        if information_source_names is not None:
            normalized_source_names = [
                source_name.strip()
                for source_name in information_source_names
                if str(source_name).strip()
            ]

        frame_count = 0
        segment_count = 0

        if include_frame_annotations:
            frame_annotations = ImageClassificationAnnotation.objects.filter(
                frame__video=video
            )
            if normalized_source_names:
                frame_annotations = frame_annotations.filter(
                    information_source__name__in=normalized_source_names
                )
            frame_count = self.add_frame_annotations(frame_annotations.distinct())

        if include_video_annotations:
            video_segments = LabelVideoSegment.objects.filter(video_file=video)
            if normalized_source_names:
                video_segments = video_segments.filter(
                    source__name__in=normalized_source_names
                )
            segment_count = self.add_video_annotations(video_segments.distinct())

        return {
            "video_id": video.pk,
            "frame_annotation_count": frame_count,
            "video_annotation_count": segment_count,
        }

    def get_related_videos_queryset(self) -> QuerySet[VideoFile]:
        from endoreg_db.models import VideoFile

        image_video_ids = self.image_annotations.values_list(
            "frame__video_id", flat=True
        )
        segment_video_ids = self.video_annotations.values_list(
            "video_file_id", flat=True
        )
        return VideoFile.objects.filter(
            pk__in=set(image_video_ids).union(set(segment_video_ids))
        ).distinct()

    def _build_frame_annotation_export(
        self,
        annotation: ImageClassificationAnnotation,
    ) -> AIDataSetFrameAnnotationExport:
        frame = annotation.frame
        video = frame.video

        file_path: str | None = None
        try:
            file_path = str(frame.file_path)
        except Exception:
            file_path = None

        return AIDataSetFrameAnnotationExport.model_validate(
            {
                "annotation_id": annotation.pk,
                "frame_id": frame.pk,
                "frame_number": frame.frame_number,
                "timestamp": frame.timestamp,
                "relative_path": frame.relative_path,
                "file_path": file_path,
                "patient_video_file_uuid": str(video.uuid),
                "video_id": video.pk,
                "video_uuid": str(video.uuid),
                "video_hash": video.video_hash,
                "original_file_name": video.original_file_name,
                "label": {
                    "id": annotation.label_id,
                    "name": annotation.label.name,
                    "labelset_name": self._resolve_labelset_name(annotation.label),
                },
                "value": annotation.value,
                "confidence": annotation.float_value,
                "annotator": annotation.annotator,
                "information_source_name": (
                    annotation.information_source.name
                    if annotation.information_source is not None
                    else None
                ),
                "model_meta_id": annotation.model_meta_id,
                "external_annotation_id": annotation.external_annotation_id,
                "date_created": annotation.date_created,
                "date_modified": annotation.date_modified,
            }
        )

    def _build_patient_videos_export(self) -> dict[str, PatientVideoFile]:
        from endoreg_db.services.lx_video_contracts import (
            build_lx_p_video_segment,
            build_lx_patient_video_file,
        )

        patient_videos: dict[str, PatientVideoFile] = {}
        segment_lists_by_video_id: dict[int, list[LabelVideoSegment]] = {}

        for segment in self.video_annotations.select_related(
            "label",
            "source",
            "video_file",
            "prediction_meta__model_meta__labelset",
            "video_file__ai_model_meta__labelset",
        ).order_by("video_file_id", "start_frame_number", "end_frame_number", "pk"):
            segment_lists_by_video_id.setdefault(segment.video_file_id, []).append(
                segment
            )

        for video in self.get_related_videos_queryset().select_related(
            "sensitive_meta",
            "state",
        ):
            patient_video = build_lx_patient_video_file(video, include_segments=False)
            attached_segments = segment_lists_by_video_id.get(video.pk, [])
            if attached_segments:
                for segment in attached_segments:
                    lx_segment = build_lx_p_video_segment(segment)
                    patient_video.patient_video_segments[str(lx_segment.uuid)] = (
                        lx_segment
                    )
            patient_videos[str(video.uuid)] = patient_video

        return patient_videos

    def build_export_payload(self) -> AIDataSetExportPayload:
        if self.pk is None:
            raise ValueError("AIDataSet must be saved before it can be exported.")

        image_annotations = list(
            self.image_annotations.select_related(
                "frame__video",
                "label",
                "information_source",
            ).order_by("frame__video_id", "frame__frame_number", "label__name", "pk")
        )
        video_annotations = list(
            self.video_annotations.select_related(
                "label",
                "source",
                "video_file",
                "prediction_meta__model_meta__labelset",
                "video_file__ai_model_meta__labelset",
            ).order_by("video_file_id", "start_frame_number", "end_frame_number", "pk")
        )

        frame_exports = [
            self._build_frame_annotation_export(annotation)
            for annotation in image_annotations
        ]
        patient_videos = self._build_patient_videos_export()

        label_ids = {
            annotation.label_id
            for annotation in image_annotations
            if annotation.label_id is not None
        }
        label_ids.update(
            segment.label_id
            for segment in video_annotations
            if segment.label_id is not None
        )

        summary = AIDataSetExportSummary.model_validate(
            {
                "image_annotation_count": len(frame_exports),
                "video_annotation_count": len(video_annotations),
                "frame_count": len(
                    {annotation.frame_id for annotation in image_annotations}
                ),
                "video_count": len(patient_videos),
                "label_count": len(label_ids),
            }
        )

        return AIDataSetExportPayload.model_validate(
            {
                "dataset_id": self.pk,
                "name": self.name,
                "description": self.description,
                "dataset_type": self.dataset_type,
                "ai_model_type": self.ai_model_type,
                "is_active": self.is_active,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "summary": summary,
                "patient_videos": patient_videos,
                "frame_annotations": frame_exports,
            }
        )

    def export_to_standardized_structure(self) -> dict[str, Any]:
        """
        Return a validated JSON-serializable export payload.
        """
        return self.build_export_payload().model_dump(mode="json")

    def __str__(self) -> str:
        if self.name:
            return f"AIDataSet(id={self.id}, name={self.name})"
        return f"AIDataSet(id={self.id})"

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime
from types import NoneType
from typing import TYPE_CHECKING, Any, TypeAlias, TypeVar

import numpy as np
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import QuerySet
from lx_dtypes.models.contracts.aidataset_export import (
    AIDataSetExportPayload,
    AIDataSetExportSummary,
    AIDataSetFrameAnnotationExport,
    AIDataSetFrameLabelExport,
)
from lx_dtypes.models.contracts.aidataset_frame_buckets import (
    AIDataSetFrameBucketCount,
    AIDataSetFrameBucketDistribution,
    AIDataSetFrameBucketSummary,
    AIDataSetLabelDistributionEntry,
    AIDataSetLabelFrameBucketCount,
    AIDataSetTargetFrameBucket,
)

from endoreg_db.schemas import (
    validate_ai_model_training_artifact_paths,
    validate_ai_model_training_request_payload,
    validate_ai_model_training_result_payload,
)
from endoreg_db.utils.validation_types import ValidationErrorMessageArg

__all__ = [
    "AIDataSet",
    "AIDataSetActiveLearningCandidateContract",
    "AIDataSetActiveLearningConfigContract",
    "AIDataSetActiveLearningSelectionContract",
    "AIDataSetExportArtifact",
    "AIDataSetExportPayload",
    "AIDataSetExportSummary",
    "AIDataSetFrameAnnotationExport",
    "AIDataSetFrameBucketCount",
    "AIDataSetFrameBucketDistribution",
    "AIDataSetFrameBucketSummary",
    "AIDataSetFrameLabelExport",
    "AIDataSetLabelDistributionEntry",
    "AIDataSetLabelFrameBucketCount",
    "AIDataSetScoredActiveLearningCandidateContract",
    "AIDataSetTargetFrameBucket",
    "AIModelTrainingRun",
]

if TYPE_CHECKING:
    from endoreg_db.models import (
        ImageClassificationAnnotation,
        Label,
        LabelSet,
        LabelVideoSegment,
        VideoFile,
    )


NoAIDataSetTextValue: TypeAlias = NoneType
NoAIDataSetValue: TypeAlias = NoneType
NoAIDataSetDateTimeValue: TypeAlias = NoneType
AIDataSetText: TypeAlias = "str | NoAIDataSetTextValue"
AIDataSetRelation: TypeAlias = "AIDataSet | NoAIDataSetValue"
AIDataSetDateTime: TypeAlias = "datetime | NoAIDataSetDateTimeValue"
_ModelT = TypeVar("_ModelT", bound=models.Model)


from lx_dtypes.models.contracts.ai_dataset import (
    AIDataSetActiveLearningCandidateContract,
    AIDataSetActiveLearningConfigContract,
    AIDataSetActiveLearningSelectionContract,
    AIDataSetScoredActiveLearningCandidateContract,
)


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

    name: models.CharField[AIDataSetText | None, Any] = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text='Human-readable identifier, e.g. "Legacy multilabel dataset v1".',
    )
    description: models.TextField[AIDataSetText | None, Any] = models.TextField(
        blank=True,
        null=True,
        help_text="Optional notes / explanation about this dataset.",
    )
    ai_model_type: models.CharField[str, Any] = models.CharField(
        max_length=255,
        default=AI_MODEL_TYPE_IMAGE_MULTILABEL,
        help_text=(
            "AI model family this dataset is for, e.g. "
            '"image_multilabel_classification".'
        ),
    )
    dataset_type: models.CharField[str, Any] = models.CharField(
        max_length=32,
        choices=DATASET_TYPE_CHOICES,
        default=DATASET_TYPE_IMAGE,
        help_text=(
            "Primary annotation modality used for training. Export helpers may "
            "still include both frame and video annotations attached to the dataset."
        ),
    )
    image_annotations: models.ManyToManyField[
        ImageClassificationAnnotation, ImageClassificationAnnotation
    ] = models.ManyToManyField(
        "ImageClassificationAnnotation",
        related_name="image_ai_datasets",
        blank=True,
        help_text=(
            "Frame-level annotations collected from the frame annotation workflow."
        ),
    )
    video_annotations: models.ManyToManyField[LabelVideoSegment, LabelVideoSegment] = (
        models.ManyToManyField(
            "LabelVideoSegment",
            related_name="video_ai_datasets",
            blank=True,
            help_text=(
                "Video-segment annotations collected from the video examination "
                "annotation workflow."
            ),
        )
    )
    created_at: models.DateTimeField[datetime, Any] = models.DateTimeField(
        auto_now_add=True,
        help_text="When this AIDataSet was created.",
    )
    updated_at: models.DateTimeField[datetime, Any] = models.DateTimeField(
        auto_now=True,
        help_text="When this AIDataSet was last modified.",
    )
    is_active: models.BooleanField[bool, Any] = models.BooleanField(
        default=True,
        help_text="Soft toggle to enable/disable this dataset for training.",
    )

    if TYPE_CHECKING:
        id: int

    @staticmethod
    def _coerce_objects(
        objects: Iterable[_ModelT] | QuerySet[_ModelT],
    ) -> list[_ModelT]:
        return list(objects)

    def get_image_annotations_queryset(self):
        return self.image_annotations

    def get_video_annotations_queryset(self):
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

    @classmethod
    def _coerce_active_learning_candidates(
        cls,
        candidates: Sequence[AIDataSetActiveLearningCandidateContract | dict[str, Any]],
    ) -> list[AIDataSetActiveLearningCandidateContract]:
        return [
            (
                candidate
                if isinstance(candidate, AIDataSetActiveLearningCandidateContract)
                else AIDataSetActiveLearningCandidateContract.model_validate(candidate)
            )
            for candidate in candidates
        ]

    @classmethod
    def _select_active_learning_candidates_locally(
        cls,
        candidates: Sequence[AIDataSetActiveLearningCandidateContract | dict[str, Any]],
        *,
        labeled_embeddings: np.ndarray | Sequence[Sequence[float]] | None = None,
        class_frequencies: np.ndarray | Sequence[float] | None = None,
        config: AIDataSetActiveLearningConfigContract | None = None,
    ) -> AIDataSetActiveLearningSelectionContract:
        from endoreg_db.services.aidataset_active_learning import (
            select_active_learning_candidates_locally,
        )

        return select_active_learning_candidates_locally(
            candidates,
            labeled_embeddings=labeled_embeddings,
            class_frequencies=class_frequencies,
            config=config,
        )

    @classmethod
    def select_active_learning_frame_indices_from_candidates(
        cls,
        candidates: Sequence[AIDataSetActiveLearningCandidateContract | dict[str, Any]],
        *,
        labeled_embeddings: np.ndarray | Sequence[Sequence[float]] | None = None,
        class_frequencies: np.ndarray | Sequence[float] | None = None,
        config: AIDataSetActiveLearningConfigContract | None = None,
    ) -> AIDataSetActiveLearningSelectionContract:
        resolved_config = config or AIDataSetActiveLearningConfigContract()
        normalized_candidates = cls._coerce_active_learning_candidates(candidates)
        reference_embeddings = (
            None
            if labeled_embeddings is None
            else np.asarray(labeled_embeddings, dtype=np.float64).tolist()
        )
        frequencies = (
            None
            if class_frequencies is None
            else np.asarray(class_frequencies, dtype=np.float64).tolist()
        )

        try:
            from lx_ai_core.active_learning import select_active_learning_candidates
        except ModuleNotFoundError as exc:
            if exc.name != "lx_ai_core":
                raise
            return cls._select_active_learning_candidates_locally(
                normalized_candidates,
                labeled_embeddings=reference_embeddings,
                class_frequencies=frequencies,
                config=resolved_config,
            )

        selection = select_active_learning_candidates(
            [candidate.model_dump(mode="json") for candidate in normalized_candidates],
            labeled_embeddings=reference_embeddings,
            class_frequencies=frequencies,
            config=resolved_config.model_dump(mode="json"),
        )
        return AIDataSetActiveLearningSelectionContract.model_validate(
            selection.model_dump(mode="json")
        )

    @classmethod
    def select_active_learning_frame_indices(
        cls,
        *,
        sample_indices: Sequence[int],
        video_ids: Sequence[int],
        frame_numbers: Sequence[int],
        probs: Sequence[Sequence[float]],
        embeddings: Sequence[Sequence[float]],
        frame_ids: Sequence[int | None] | None = None,
        timestamps: Sequence[float | None] | None = None,
        quality_scores: Sequence[float | None] | None = None,
        labeled_embeddings: np.ndarray | Sequence[Sequence[float]] | None = None,
        class_frequencies: np.ndarray | Sequence[float] | None = None,
        config: AIDataSetActiveLearningConfigContract | None = None,
    ) -> AIDataSetActiveLearningSelectionContract:
        candidate_count = len(sample_indices)
        if not (
            len(video_ids)
            == len(frame_numbers)
            == len(probs)
            == len(embeddings)
            == candidate_count
        ):
            raise ValueError("All active learning arrays must have the same length.")

        if frame_ids is None or quality_scores is None:
            raise ValueError(
                "frame_ids and quality_scores are required by "
                "AIDataSetActiveLearningCandidateContract."
            )

        def _required_int_values(
            values: Sequence[int | None],
            *,
            name: str,
        ) -> list[int]:
            result: list[int] = []
            for value in values:
                if value is None:
                    raise ValueError(f"{name} must not contain None values.")
                result.append(int(value))
            return result

        def _required_float_values(
            values: Sequence[float | None],
            *,
            name: str,
        ) -> list[float]:
            result: list[float] = []
            for value in values:
                if value is None:
                    raise ValueError(f"{name} must not contain None values.")
                result.append(float(value))
            return result

        resolved_frame_ids = _required_int_values(frame_ids, name="frame_ids")
        resolved_timestamps = (
            _required_float_values(timestamps, name="timestamps")
            if timestamps is not None
            else [float(frame_number) for frame_number in frame_numbers]
        )
        resolved_quality_scores = _required_float_values(
            quality_scores, name="quality_scores"
        )

        candidates = [
            AIDataSetActiveLearningCandidateContract(
                sample_index=sample_indices[idx],
                frame_id=resolved_frame_ids[idx],
                video_id=video_ids[idx],
                frame_number=frame_numbers[idx],
                timestamp=resolved_timestamps[idx],
                probs=list(probs[idx]),
                embedding=list(embeddings[idx]),
                quality_score=resolved_quality_scores[idx],
            )
            for idx in range(candidate_count)
        ]

        return cls.select_active_learning_frame_indices_from_candidates(
            candidates,
            labeled_embeddings=labeled_embeddings,
            class_frequencies=class_frequencies,
            config=config,
        )

    def add_frame_annotations(
        self,
        annotations: (
            Iterable[ImageClassificationAnnotation]
            | QuerySet[ImageClassificationAnnotation]
        ),
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

    def build_frame_bucket_distribution(
        self,
        *,
        label_set: LabelSet | None = None,
        target_label: Label | None = None,
        prediction_segments_only: bool = True,
    ) -> AIDataSetFrameBucketDistribution:
        from endoreg_db.services.aidataset_frame_buckets import (
            build_frame_bucket_distribution,
        )

        return build_frame_bucket_distribution(
            self,
            label_set=label_set,
            target_label=target_label,
            prediction_segments_only=prediction_segments_only,
        )

    def build_export_payload(
        self,
        *,
        center_key: str | None = None,
        all_centers: bool = False,
        only_validated: bool = False,
    ) -> AIDataSetExportPayload:
        from endoreg_db.services.aidataset_exports import build_export_payload

        return build_export_payload(
            self,
            center_key=center_key,
            all_centers=all_centers,
            only_validated=only_validated,
        )

    def export_to_standardized_structure(
        self,
        *,
        center_key: str | None = None,
        all_centers: bool = False,
        only_validated: bool = False,
    ) -> dict[str, Any]:
        from endoreg_db.services.aidataset_exports import (
            export_to_standardized_structure,
        )

        return export_to_standardized_structure(
            self,
            center_key=center_key,
            all_centers=all_centers,
            only_validated=only_validated,
        )

    def __str__(self) -> str:
        if self.name:
            return f"AIDataSet(id={self.id}, name={self.name})"
        return f"AIDataSet(id={self.id})"


class AIModelTrainingRun(models.Model):
    STATUS_QUEUED = "queued"
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_LOST = "lost"
    TERMINAL_STATUSES = frozenset({STATUS_COMPLETED, STATUS_FAILED, STATUS_LOST})
    STATUS_CHOICES = [
        (STATUS_QUEUED, "Queued"),
        (STATUS_RUNNING, "Running"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
        (STATUS_LOST, "Lost"),
    ]

    run_id: models.UUIDField[uuid.UUID, Any] = models.UUIDField(
        default=uuid.uuid4, editable=False, unique=True
    )
    dataset: models.ForeignKey[Any] = models.ForeignKey(
        AIDataSet,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="model_training_runs",
    )
    dataset_name: models.CharField[AIDataSetText | None, Any] = models.CharField(
        max_length=255, blank=True, null=True
    )
    dataset_type: models.CharField[str, Any] = models.CharField(
        max_length=32, blank=True
    )
    ai_model_type: models.CharField[str, Any] = models.CharField(
        max_length=255, blank=True
    )
    backbone_name: models.CharField[str, Any] = models.CharField(max_length=128)
    feature_mode: models.CharField[str, Any] = models.CharField(max_length=64)
    freeze_backbone: models.BooleanField[bool, Any] = models.BooleanField(default=True)
    epochs: models.PositiveIntegerField[int, Any] = models.PositiveIntegerField(
        default=10
    )
    batch_size: models.PositiveIntegerField[int, Any] = models.PositiveIntegerField(
        default=32
    )
    labelset_version: models.PositiveIntegerField[int, Any] = (
        models.PositiveIntegerField(default=1)
    )
    treat_unlabeled_as_negative: models.BooleanField[bool, Any] = models.BooleanField(
        default=True
    )
    backbone_checkpoint: models.TextField[AIDataSetText, Any] = models.TextField(
        blank=True, null=True
    )
    request_payload: models.JSONField[Any, Any] = models.JSONField(
        default=dict, blank=True
    )
    command_kwargs: models.JSONField[Any, Any] = models.JSONField(
        default=dict, blank=True
    )
    status: models.CharField[str, Any] = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_QUEUED,
        db_index=True,
    )
    server_instance_id: models.CharField[str, Any] = models.CharField(
        max_length=64, blank=True, db_index=True
    )
    result: models.JSONField[Any, Any] = models.JSONField(blank=True, null=True)
    artifact_paths: models.JSONField[dict[str, str]] = models.JSONField(
        default=dict, blank=True
    )
    error: models.TextField[str, Any] = models.TextField(blank=True)
    stdout: models.TextField[str, Any] = models.TextField(blank=True)
    stderr: models.TextField[str, Any] = models.TextField(blank=True)
    created_at: models.DateTimeField[datetime, Any] = models.DateTimeField(
        auto_now_add=True
    )
    updated_at: models.DateTimeField[datetime, Any] = models.DateTimeField(
        auto_now=True
    )
    started_at: models.DateTimeField[AIDataSetDateTime, Any] = models.DateTimeField(
        blank=True, null=True
    )
    finished_at: models.DateTimeField[AIDataSetDateTime, Any] = models.DateTimeField(
        blank=True, null=True
    )

    class Meta:
        indexes = [
            models.Index(fields=["status", "-created_at"], name="aid_train_status_idx"),
            models.Index(
                fields=["server_instance_id", "status"],
                name="aid_train_server_idx",
            ),
        ]
        ordering = ["-created_at", "-id"]

    @property
    def run_key(self) -> str:
        return self.run_id.hex

    @property
    def is_terminal(self) -> bool:
        return self.status in self.TERMINAL_STATUSES

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationErrorMessageArg] = {}
        try:
            self.request_payload = validate_ai_model_training_request_payload(
                self.request_payload
            )
        except ValueError as exc:
            errors["request_payload"] = str(exc)
        try:
            self.result = validate_ai_model_training_result_payload(self.result)
        except ValueError as exc:
            errors["result"] = str(exc)
        try:
            self.artifact_paths = validate_ai_model_training_artifact_paths(
                self.artifact_paths
            )
        except ValueError as exc:
            errors["artifact_paths"] = str(exc)
        if errors:
            raise ValidationError(errors)

    def save(self, *args: object, **kwargs: object) -> None:
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"AIModelTrainingRun(run_id={self.run_key}, status={self.status})"


class AIDataSetExportArtifact(models.Model):
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_RUNNING, "Running"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
    ]

    artifact_id: models.UUIDField[uuid.UUID, Any] = models.UUIDField(
        default=uuid.uuid4, editable=False, unique=True
    )
    dataset: models.ForeignKey[Any] = models.ForeignKey(
        AIDataSet,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="export_artifacts",
    )
    dataset_name: models.CharField[AIDataSetText | None, Any] = models.CharField(
        max_length=255, blank=True, null=True
    )
    dataset_type: models.CharField[str, Any] = models.CharField(
        max_length=32, blank=True
    )
    ai_model_type: models.CharField[str, Any] = models.CharField(
        max_length=255, blank=True
    )
    request_payload: models.JSONField[Any, Any] = models.JSONField(
        default=dict, blank=True
    )
    center_key: models.CharField[AIDataSetText | None, Any] = models.CharField(
        max_length=255, blank=True, null=True
    )
    all_centers: models.BooleanField[bool, Any] = models.BooleanField(default=False)
    only_validated: models.BooleanField[bool, Any] = models.BooleanField(default=True)
    status: models.CharField[str, Any] = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_RUNNING,
        db_index=True,
    )
    output_path: models.TextField[str, Any] = models.TextField(blank=True)
    download_filename: models.CharField[str, Any] = models.CharField(
        max_length=255, blank=True
    )
    sha256: models.CharField[str, Any] = models.CharField(max_length=64, blank=True)
    byte_size: models.PositiveBigIntegerField[int, Any] = (
        models.PositiveBigIntegerField(default=0)
    )
    summary: models.JSONField[Any, Any] = models.JSONField(default=dict, blank=True)
    error: models.TextField[str, Any] = models.TextField(blank=True)
    created_at: models.DateTimeField[datetime, Any] = models.DateTimeField(
        auto_now_add=True
    )
    updated_at: models.DateTimeField[datetime, Any] = models.DateTimeField(
        auto_now=True
    )
    finished_at: models.DateTimeField[AIDataSetDateTime, Any] = models.DateTimeField(
        blank=True, null=True
    )

    class Meta:
        indexes = [
            models.Index(
                fields=["status", "-created_at"], name="aid_export_status_idx"
            ),
            models.Index(
                fields=["dataset", "-created_at"], name="aid_export_dataset_idx"
            ),
        ]
        ordering = ["-created_at", "-id"]

    @property
    def artifact_key(self) -> str:
        return self.artifact_id.hex

    def __str__(self) -> str:
        return (
            f"AIDataSetExportArtifact(artifact_id={self.artifact_key}, "
            f"status={self.status})"
        )

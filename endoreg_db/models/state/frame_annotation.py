# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
from __future__ import annotations

import logging
import random
from hashlib import sha256
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from types import NoneType
from typing import (
    TYPE_CHECKING,
    Iterable,
    Iterator,
    Protocol,
    TypeAlias,
    TypedDict,
    cast,
)

from django.db.models import Q, QuerySet

from endoreg_db.services.video_files import get_or_create_video_state
from endoreg_db.utils.media_urls import build_video_frame_stream_path
from endoreg_db.utils.rust_backend import (
    derive_frame_annotation_status,
    normalize_frame_sampling_strategy_token,
    normalize_frame_task_mode_token,
)
from lx_dtypes.models.contracts.frame_annotation import (
    FrameAnnotationAnnotationPayload,
    FrameAnnotationLabelOptionPayload,
    FrameAnnotationTaskPayload,
)
from endoreg_db.models.label import LabelVideoSegment

if TYPE_CHECKING:
    from endoreg_db.models import AIDataSet, Frame, VideoFile
    from endoreg_db.models.label import ImageClassificationAnnotation, Label, LabelSet


class RequestUserLike(Protocol):
    is_authenticated: bool
    username: str


class RequestLike(Protocol):
    user: RequestUserLike


class FrameLike(Protocol):
    id: int
    video_id: int
    frame_number: int
    relative_path: str
    image_classification_annotations: AnnotationQuerySetLike


class LabelLike(Protocol):
    @property
    def id(self) -> int: ...

    @property
    def pk(self) -> int: ...

    @property
    def name(self) -> str: ...


class LabelQuerySetLike(Protocol):
    def all(self) -> "LabelQuerySetLike": ...
    def order_by(self, *args: object, **kwargs: object) -> "LabelQuerySetLike": ...
    def filter(self, *args: object, **kwargs: object) -> "LabelQuerySetLike": ...
    def exists(self) -> bool: ...
    def __iter__(self) -> Iterable[LabelLike]: ...


class LabelSetLike(Protocol):
    @property
    def labels(self) -> "LabelQuerySetLike": ...


class AIDataSetLike(Protocol):
    @property
    def dataset_type(self) -> str: ...

    @property
    def ai_model_type(self) -> str: ...

    @property
    def image_annotations(self) -> AnnotationQuerySetLike: ...

    @property
    def video_annotations(self) -> VideoAnnotationQuerySetLike: ...


class VideoFileLike(Protocol):
    @property
    def state(self) -> object | None: ...


class LabelSetQuerySetLike(Protocol):
    def all(self) -> "LabelSetQuerySetLike": ...
    def order_by(self, *args: object, **kwargs: object) -> "LabelSetQuerySetLike": ...
    def filter(self, *args: object, **kwargs: object) -> "LabelSetQuerySetLike": ...
    def exists(self) -> bool: ...
    def __iter__(self) -> Iterable[LabelLike]: ...


class AnnotationQuerySetLike(Protocol):
    def select_related(
        self, *args: object, **kwargs: object
    ) -> "AnnotationQuerySetLike": ...
    def filter(self, *args: object, **kwargs: object) -> "AnnotationQuerySetLike": ...
    def exclude(self, *args: object, **kwargs: object) -> "AnnotationQuerySetLike": ...
    def __iter__(self) -> Iterator[ImageClassificationAnnotationLike]: ...
    def iterator(self) -> Iterable[ImageClassificationAnnotationLike]: ...
    def exists(self) -> bool: ...
    def values_list(
        self, *args: object, **kwargs: object
    ) -> Iterable[tuple[int, int]]: ...
    def order_by(self, *args: object, **kwargs: object) -> "AnnotationQuerySetLike": ...
    def distinct(self) -> "AnnotationQuerySetLike": ...
    def count(self) -> int: ...


class VideoAnnotationQuerySetLike(Protocol):
    def select_related(
        self, *args: object, **kwargs: object
    ) -> "VideoAnnotationQuerySetLike": ...
    def filter(
        self, *args: object, **kwargs: object
    ) -> "VideoAnnotationQuerySetLike": ...
    def exclude(
        self, *args: object, **kwargs: object
    ) -> "VideoAnnotationQuerySetLike": ...
    def iterator(self) -> Iterable[LabelVideoSegmentLike]: ...
    def exists(self) -> bool: ...
    def order_by(
        self, *args: object, **kwargs: object
    ) -> "VideoAnnotationQuerySetLike": ...
    def distinct(self) -> "VideoAnnotationQuerySetLike": ...


class ImageClassificationAnnotationLike(Protocol):
    id: int
    label_id: int
    label: LabelLike
    value: bool
    float_value: float | None
    annotator: str | None
    information_source: object | None
    model_meta_id: int | None
    external_annotation_id: str | None
    frame_id: int


class FrameModelLike(Protocol):
    id: int
    video_id: int
    frame_number: int
    relative_path: str
    image_classification_annotations: QuerySet["ImageClassificationAnnotation"]


class LabelSetModelLike(Protocol):
    labels: QuerySet["Label"]


class ImageClassificationAnnotationModelLike(Protocol):
    id: int
    label_id: int
    label: LabelLike
    value: bool
    float_value: float | None
    annotator: str | None
    information_source: object | None
    model_meta_id: int | None
    external_annotation_id: str | None
    frame_id: int


class LabelVideoSegmentModelLike(Protocol):
    pk: int
    label: LabelLike | None
    label_id: int
    source_id: int | None
    start_frame_number: int
    end_frame_number: int
    video_file_id: int

    def get_model_meta(self) -> object | None: ...
    def get_frames(self) -> QuerySet["Frame"]: ...


class LabelVideoSegmentLike(Protocol):
    pk: int
    label: LabelLike | None
    label_id: int
    source_id: int | None
    start_frame_number: int
    end_frame_number: int

    def get_model_meta(self) -> object | None: ...
    def get_frames(self) -> QuerySet["Frame"]: ...


class FrameAnnotationImageAnnotationLike(Protocol):
    @property
    def id(self) -> int: ...

    @property
    def label(self) -> LabelLike: ...

    @property
    def label_id(self) -> int: ...

    @property
    def value(self) -> bool: ...

    @property
    def frame_id(self) -> int: ...

    @property
    def annotator(self) -> str | None: ...

    @property
    def information_source(self) -> object | None: ...

    @property
    def model_meta_id(self) -> int | None: ...

    @property
    def external_annotation_id(self) -> str | None: ...


class FrameAnnotationVideoAnnotationLike(Protocol):
    @property
    def label(self) -> LabelLike: ...

    @property
    def label_id(self) -> int: ...

    @property
    def value(self) -> bool: ...

    @property
    def frame_id(self) -> int: ...

    @property
    def video_file_id(self) -> int: ...

    @property
    def source(self) -> FrameAnnotationSource: ...

    @property
    def prediction_meta_id(self) -> int | None: ...

    def get_model_meta(self) -> object | None: ...


class SegmentAnnotationSnapshot(TypedDict):
    video: VideoFile
    start_frame_number: int
    end_frame_number: int
    label: Label | None
    information_source_id: int | None
    model_meta_id: int | None


logger = logging.getLogger(__name__)

DEFAULT_FRAME_INFORMATION_SOURCE_NAME = "manual_annotation"
SEGMENT_DERIVED_EXTERNAL_ANNOTATION_PREFIX = "segment-derived:v1"
PHI_REGION_DATASET_MODEL_TYPE = "phi_region_detector"

PREDICTION_INFORMATION_SOURCE_NAMES = {
    "prediction",
    "default_prediction",
    "prediction_annotation",
}

MANUAL_ANNOTATION_INFORMATION_SOURCE_NAMES = {
    "annotation",
    "default_annotation",
    "frame_annotation_frontend",
    "human_annotation",
    "lx_anonymizer_evaluation",
    "manual_annotation",
}

NoPredictionMetaIdValue: TypeAlias = NoneType
NoFrameAnnotationSourceValue: TypeAlias = NoneType
PredictionMetaId: TypeAlias = "int | NoPredictionMetaIdValue"
FrameAnnotationSource: TypeAlias = (
    "FrameAnnotationSourceName | NoFrameAnnotationSourceValue"
)


class FrameAnnotationSourceName(Protocol):
    name: str


FrameAnnotationSourceInput: TypeAlias = (
    "str | FrameAnnotationSourceName | NoFrameAnnotationSourceValue"
)


class PredictionSegmentLike(Protocol):
    @property
    def source(self) -> FrameAnnotationSource: ...

    @property
    def prediction_meta_id(self) -> PredictionMetaId: ...


class FrameAnnotationStatus(str, Enum):
    NOT_STARTED = "not_started"
    FRAMES_UNAVAILABLE = "frames_unavailable"
    PREDICTION_PENDING = "prediction_pending"
    PREDICTION_READY = "prediction_ready"
    ANNOTATION_READY = "annotation_ready"
    ANNOTATION_IN_PROGRESS = "annotation_in_progress"
    ANNOTATION_COMPLETE = "annotation_complete"
    STALE = "stale"

    def __str__(self) -> str:
        return self.value


class FrameTaskMode(str, Enum):
    RANDOM = "random"
    FILTERED = "filtered"

    def __str__(self) -> str:
        return self.value


class FrameSamplingStrategy(str, Enum):
    BALANCED = "balanced"
    SEGMENTS = "segments"
    ANNOTATIONS = "annotations"
    NONE = "none"

    def __str__(self) -> str:
        return self.value


SUPPORTED_FRAME_TASK_MODES = {mode.value for mode in FrameTaskMode}
SUPPORTED_FRAME_SAMPLING_STRATEGIES = {
    strategy.value for strategy in FrameSamplingStrategy
}


def normalize_frame_task_mode(value: object) -> FrameTaskMode:
    parsed = str(value or FrameTaskMode.RANDOM.value).strip().lower()
    rust_value = normalize_frame_task_mode_token(parsed)
    if rust_value is not None:
        return FrameTaskMode(rust_value)
    if parsed == FrameTaskMode.FILTERED.value:
        return FrameTaskMode.FILTERED
    return FrameTaskMode.RANDOM


def normalize_frame_sampling_strategy(value: object) -> FrameSamplingStrategy:
    parsed = str(value or FrameSamplingStrategy.BALANCED.value).strip().lower()
    rust_value = normalize_frame_sampling_strategy_token(parsed)
    if rust_value is not None:
        return FrameSamplingStrategy(rust_value)
    for strategy in FrameSamplingStrategy:
        if parsed == strategy.value:
            return strategy
    return FrameSamplingStrategy.BALANCED


def normalize_frame_annotator(annotator: str | None) -> str | None:
    if annotator is None:
        return None
    normalized = str(annotator).strip()
    return normalized or None


def resolve_request_annotator(
    request: RequestLike,
    requested_annotator: str | None = None,
) -> str:
    normalized = normalize_frame_annotator(requested_annotator)
    if normalized is not None:
        return normalized
    if request.user and request.user.is_authenticated:
        return str(request.user.username)
    return ""


def resolve_frame_information_source_name(value: object) -> str:
    source_value = getattr(value, "name", value)
    source_name = str(source_value or DEFAULT_FRAME_INFORMATION_SOURCE_NAME).strip()
    return source_name or DEFAULT_FRAME_INFORMATION_SOURCE_NAME


def resolve_ai_dataset_for_queue(
    *,
    dataset_name_raw: object,
    dataset_type_raw: object,
    dataset_id_raw: object = None,
) -> AIDataSet | None:
    from endoreg_db.models import AIDataSet
    from endoreg_db.utils.set_default_center import get_application_settings

    if dataset_id_raw not in (None, ""):
        try:
            dataset_id = int(str(dataset_id_raw).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError("ai_dataset_id must be an integer.") from exc
        dataset = AIDataSet.objects.filter(pk=dataset_id).first()
        if dataset is None:
            raise ValueError("Unknown ai_dataset_id.")
        return dataset

    settings_obj = get_application_settings()
    dataset_name = (
        str(dataset_name_raw).strip()
        if dataset_name_raw is not None
        else str(settings_obj.ai_dataset_name or "").strip()
    )
    dataset_type = (
        str(dataset_type_raw).strip().lower()
        if dataset_type_raw is not None
        else str(settings_obj.ai_dataset_type or "").strip().lower()
    )

    if not dataset_name and not dataset_type:
        dataset = AIDataSet.objects.first()
        return dataset

    dataset_qs = AIDataSet.objects.all()
    if dataset_name:
        dataset_qs = dataset_qs.filter(name=dataset_name)
    if dataset_type:
        dataset_qs = dataset_qs.filter(dataset_type=dataset_type)
    return dataset_qs.order_by("-updated_at", "-pk").first()


def ai_dataset_requires_raw_frames(dataset: AIDataSet | None) -> bool:
    if dataset is None:
        return False
    return (
        str(getattr(dataset, "ai_model_type", "") or "").strip().lower()
        == PHI_REGION_DATASET_MODEL_TYPE
    )


def mark_frame_prediction_reset(video: VideoFile) -> None:
    state = get_or_create_video_state(video)
    state.initial_prediction_completed = False
    state.lvs_created = False
    state.frame_annotations_generated = False
    state.save(
        update_fields=[
            "initial_prediction_completed",
            "lvs_created",
            "frame_annotations_generated",
            "date_modified",
        ]
    )


def mark_frame_prediction_completed(video: VideoFile) -> None:
    state = get_or_create_video_state(video)
    state.initial_prediction_completed = True
    state.save(update_fields=["initial_prediction_completed", "date_modified"])


def mark_prediction_segments_created(video: VideoFile, *, created: bool) -> None:
    state = get_or_create_video_state(video)
    state.lvs_created = bool(created)
    state.save(update_fields=["lvs_created", "date_modified"])


def mark_frame_annotations_generated(video: VideoFile) -> None:
    state = get_or_create_video_state(video)
    state.frame_annotations_generated = True
    state.save(update_fields=["frame_annotations_generated", "date_modified"])


def mark_frame_annotations_stale(video: VideoFile) -> None:
    state = get_or_create_video_state(video)
    state.frame_annotations_generated = False
    state.save(update_fields=["frame_annotations_generated", "date_modified"])


def resolve_frame_annotation_status(video: VideoFile) -> str:
    state = getattr(video, "state", None)
    if state is None:
        rust_status = derive_frame_annotation_status(
            has_state=False,
            frames_extracted=False,
            initial_prediction_completed=False,
            lvs_created=False,
            frame_annotations_generated=False,
        )
        return rust_status or FrameAnnotationStatus.NOT_STARTED.value

    frames_extracted = bool(getattr(state, "frames_extracted", False))
    initial_prediction_completed = bool(
        getattr(state, "initial_prediction_completed", False)
    )
    lvs_created = bool(getattr(state, "lvs_created", False))
    frame_annotations_generated = bool(
        getattr(state, "frame_annotations_generated", False)
    )
    rust_status = derive_frame_annotation_status(
        has_state=True,
        frames_extracted=frames_extracted,
        initial_prediction_completed=initial_prediction_completed,
        lvs_created=lvs_created,
        frame_annotations_generated=frame_annotations_generated,
    )
    if rust_status is not None:
        return rust_status
    if not frames_extracted:
        return FrameAnnotationStatus.FRAMES_UNAVAILABLE.value
    if not initial_prediction_completed:
        return FrameAnnotationStatus.PREDICTION_PENDING.value
    if initial_prediction_completed and not lvs_created:
        return FrameAnnotationStatus.PREDICTION_READY.value
    if frame_annotations_generated:
        return FrameAnnotationStatus.ANNOTATION_COMPLETE.value
    return FrameAnnotationStatus.ANNOTATION_READY.value


def validated_annotators_for_video(video: VideoFile) -> list[str]:
    from endoreg_db.models import ImageClassificationAnnotation

    annotators = (
        ImageClassificationAnnotation.objects.filter(frame__video=video)
        .exclude(annotator__isnull=True)
        .exclude(annotator__exact="")
        .order_by("annotator")
        .values_list("annotator", flat=True)
        .distinct()
    )
    return [annotator for annotator in annotators if annotator]


def _label_allowed_by_set(label_id: int | None, label_set: LabelSet | None) -> bool:
    if label_id is None:
        return False
    if label_set is None:
        return True
    return label_set.labels.filter(pk=label_id).exists()


def is_prediction_segment(segment: object) -> bool:
    source = cast(FrameAnnotationSourceName | None, getattr(segment, "source", None))
    source_name = (source.name if source else "").strip().lower()
    prediction_meta_id = getattr(segment, "prediction_meta_id", None)
    return (
        prediction_meta_id is not None
        or source_name in PREDICTION_INFORMATION_SOURCE_NAMES
        or source_name.startswith("prediction")
        or source_name.startswith("model")
    )


def segment_derived_external_annotation_id(
    *,
    segment_id: int | None,
    frame_id: int | None,
    label_id: int | None,
    information_source_id: int | None,
    model_meta_id: int | None,
    annotator: str | None = None,
) -> str:
    normalized_parts = [
        str(segment_id or ""),
        str(frame_id or ""),
        str(label_id or ""),
        str(information_source_id or ""),
        str(model_meta_id or ""),
        str(annotator or ""),
    ]
    digest = sha256("|".join(normalized_parts).encode("utf-8")).hexdigest()[:24]
    return (
        f"{SEGMENT_DERIVED_EXTERNAL_ANNOTATION_PREFIX}:"
        f"{segment_id or 'none'}:{frame_id or 'none'}:{digest}"
    )


def segment_derived_external_annotation_prefix_for_segment(
    segment_id: int,
) -> str:
    return f"{SEGMENT_DERIVED_EXTERNAL_ANNOTATION_PREFIX}:{segment_id}:"


def is_segment_derived_external_annotation_id(value: object) -> bool:
    return isinstance(value, str) and value.startswith(
        f"{SEGMENT_DERIVED_EXTERNAL_ANNOTATION_PREFIX}:"
    )


def non_segment_derived_annotation_filter() -> Q:
    return (
        Q(external_annotation_id__isnull=True)
        | Q(external_annotation_id__exact="")
        | ~Q(
            external_annotation_id__startswith=(
                f"{SEGMENT_DERIVED_EXTERNAL_ANNOTATION_PREFIX}:"
            )
        )
    )


def prediction_annotation_filter() -> Q:
    return (
        Q(information_source__information_source_types__name="prediction")
        | Q(information_source__name__in=PREDICTION_INFORMATION_SOURCE_NAMES)
        | Q(model_meta_id__isnull=False)
    )


def manual_annotation_filter(
    information_source_name: str | None = None,
) -> Q:
    if information_source_name:
        return Q(information_source__name=information_source_name)
    return Q(
        information_source__information_source_types__name__in=[
            "annotation",
            "manual_annotation",
        ]
    ) | Q(information_source__name__in=MANUAL_ANNOTATION_INFORMATION_SOURCE_NAMES)


def manual_frame_annotation_preference_filter() -> Q:
    return manual_annotation_filter() & non_segment_derived_annotation_filter()


def _build_frame_task_queryset(
    *,
    video_id: int | None,
    filter_label_id: int | None,
    information_source_name: str,
    annotator: str,
    exclude_annotated: bool,
    target_label_id: int | None,
    require_extracted_frames: bool = True,
    require_raw_video: bool = False,
    require_processed_video: bool = False,
    require_streamable_video_artifact: bool = False,
    exclude_frame_ids: set[int] | None = None,
    candidate_frame_ids: set[int] | None = None,
) -> QuerySet[Frame]:
    from endoreg_db.models import Frame

    frames_qs: QuerySet[Frame] = Frame.objects.select_related("video")
    if require_extracted_frames:
        frames_qs = frames_qs.filter(is_extracted=True)
    if video_id is not None:
        frames_qs = frames_qs.filter(video_id=video_id)
    if require_raw_video:
        frames_qs = frames_qs.exclude(video__raw_file__isnull=True).exclude(
            video__raw_file__exact=""
        )
    if require_processed_video:
        frames_qs = frames_qs.exclude(video__processed_file__isnull=True).exclude(
            video__processed_file__exact=""
        )
    if require_streamable_video_artifact:
        frames_qs = frames_qs.filter(
            Q(video__raw_file__isnull=False) & ~Q(video__raw_file__exact="")
            | Q(video__processed_file__isnull=False)
            & ~Q(video__processed_file__exact="")
        )
    if candidate_frame_ids is not None:
        if not candidate_frame_ids:
            return frames_qs.none()
        frames_qs = frames_qs.filter(id__in=candidate_frame_ids)

    if filter_label_id is not None:
        frames_qs = frames_qs.filter(
            image_classification_annotations__label_id=filter_label_id,
            image_classification_annotations__value=True,
        )

    if exclude_annotated:
        annotation_filter: dict[str, object] = {
            "image_classification_annotations__information_source__name": information_source_name
        }
        if annotator:
            annotation_filter["image_classification_annotations__annotator"] = annotator
        if target_label_id is not None:
            annotation_filter["image_classification_annotations__label_id"] = (
                target_label_id
            )
        frames_qs = frames_qs.exclude(**annotation_filter)

    if exclude_frame_ids:
        frames_qs = frames_qs.exclude(id__in=exclude_frame_ids)

    return frames_qs.order_by("id").distinct()


@dataclass(frozen=True)
class FrameAnnotationQueueSpec:
    limit: int
    task_mode: FrameTaskMode = FrameTaskMode.RANDOM
    video_id: int | None = None
    label_set: LabelSet | None = None
    target_label: Label | None = None
    filter_label: Label | None = None
    information_source_name: str = DEFAULT_FRAME_INFORMATION_SOURCE_NAME
    annotator: str = ""
    exclude_annotated: bool = True
    ai_dataset: AIDataSet | None = None
    sampling_strategy: FrameSamplingStrategy = FrameSamplingStrategy.BALANCED
    prediction_segments_only: bool = True
    exclude_frame_ids: set[int] = field(default_factory=set)
    require_extracted_frames: bool = True
    require_raw_video: bool = False
    require_processed_video: bool = False
    require_streamable_video_artifact: bool = False


@dataclass(frozen=True)
class FrameAnnotationQueueResult:
    tasks: list[FrameAnnotationTaskPayload]
    selection_strategy: str
    label_distribution: list[dict[str, int]] = field(default_factory=list)
    selected_label_counts: dict[str, int] = field(default_factory=dict)
    segment_bucket_counts: dict[str, int] = field(default_factory=dict)
    annotation_bucket_counts: dict[str, int] = field(default_factory=dict)
    bucket_counts: dict[str, int] = field(default_factory=dict)


def _pick_random_frame(
    *,
    spec: FrameAnnotationQueueSpec,
    exclude_frame_ids: set[int] | None = None,
    candidate_frame_ids: set[int] | None = None,
) -> FrameLike | None:
    frames_qs = _build_frame_task_queryset(
        video_id=spec.video_id,
        filter_label_id=cast(int | None, getattr(spec.filter_label, "id", None))
        if spec.filter_label is not None
        else None,
        information_source_name=resolve_frame_information_source_name(
            spec.information_source_name
        ),
        annotator=spec.annotator,
        exclude_annotated=spec.exclude_annotated,
        target_label_id=cast(int | None, getattr(spec.target_label, "id", None))
        if spec.target_label is not None
        else None,
        require_extracted_frames=spec.require_extracted_frames,
        require_raw_video=(
            spec.require_raw_video or ai_dataset_requires_raw_frames(spec.ai_dataset)
        ),
        require_processed_video=spec.require_processed_video,
        require_streamable_video_artifact=spec.require_streamable_video_artifact,
        exclude_frame_ids=exclude_frame_ids,
        candidate_frame_ids=candidate_frame_ids,
    )
    count = frames_qs.count()
    if count == 0:
        return None
    offset = random.randint(0, count - 1)
    return cast(FrameLike, frames_qs[offset])


def _build_dataset_target_buckets(
    *,
    dataset: AIDataSet | None,
    target_label: Label | None,
    require_extracted_frames: bool,
) -> dict[str, set[int]]:
    from endoreg_db.models import AIDataSet

    if dataset is None:
        return {}
    if dataset.dataset_type != AIDataSet.DATASET_TYPE_IMAGE:
        return {}
    if target_label is None:
        return {}

    annotations = dataset.image_annotations.select_related("frame", "label").filter(
        frame__isnull=False,
    )
    if require_extracted_frames:
        annotations = annotations.filter(frame__is_extracted=True)
    if not annotations.exists():
        return {}

    frame_ids_by_bucket: dict[str, set[int]] = {
        "positive": set(),
        "negative": set(),
        "unknown": set(),
    }
    seen_frame_ids: set[int] = set()
    target_values_by_frame_id: dict[int, list[bool]] = defaultdict(list)

    for annotation in cast(
        Iterable[ImageClassificationAnnotationLike], annotations.iterator()
    ):
        frame_id = cast(int, getattr(annotation, "frame_id"))
        label_id = cast(int, getattr(annotation, "label_id"))
        seen_frame_ids.add(frame_id)
        if label_id == target_label.pk:
            target_values_by_frame_id[frame_id].append(annotation.value)

    for frame_id in seen_frame_ids:
        target_values = target_values_by_frame_id.get(frame_id, [])
        if any(target_values):
            frame_ids_by_bucket["positive"].add(frame_id)
        elif target_values:
            frame_ids_by_bucket["negative"].add(frame_id)
        else:
            frame_ids_by_bucket["unknown"].add(frame_id)

    return {
        bucket_name: frame_ids
        for bucket_name, frame_ids in frame_ids_by_bucket.items()
        if frame_ids
    }


def _build_dataset_label_distribution(
    *,
    dataset: AIDataSet | None,
    label_set: LabelSet | None,
) -> dict[int, dict[str, int]]:
    if dataset is None:
        return {}

    distribution: dict[int, dict[str, int]] = {}

    def ensure_label(label: LabelLike | None) -> dict[str, int] | None:
        if label is None:
            return None
        label_id = label.pk
        if not _label_allowed_by_set(label_id, label_set):
            return None
        entry = distribution.setdefault(
            label_id,
            {
                "label_id": label_id,
                "frame_positive": 0,
                "frame_negative": 0,
                "segment_count": 0,
                "total": 0,
            },
        )
        return entry

    for annotation in cast(
        Iterable[ImageClassificationAnnotationLike],
        dataset.image_annotations.select_related("label")
        .filter(label__isnull=False, frame__is_extracted=True)
        .iterator(),
    ):
        entry = ensure_label(cast(LabelLike | None, getattr(annotation, "label")))
        if entry is None:
            continue
        if annotation.value:
            entry["frame_positive"] += 1
        else:
            entry["frame_negative"] += 1
        entry["total"] += 1

    for segment in cast(
        Iterable[LabelVideoSegment],
        dataset.video_annotations.select_related("label")
        .filter(label__isnull=False)
        .iterator(),
    ):
        entry = ensure_label(cast(LabelLike | None, getattr(segment, "label")))
        if entry is None:
            continue
        entry["segment_count"] += 1
        entry["total"] += 1

    return distribution


def serialize_label_distribution(
    distribution: dict[int, dict[str, int]],
) -> list[dict[str, int]]:
    return sorted(
        [{"label_id": label_id, **entry} for label_id, entry in distribution.items()],
        key=lambda item: (item["total"], item["label_id"]),
    )


def _build_balanced_label_order(
    *,
    label_set: LabelSet | None,
    target_label: Label | None,
    distribution: dict[int, dict[str, int]],
) -> list[int]:
    if label_set is not None:
        labels = list(
            cast(Iterable[LabelLike], label_set.labels.all().order_by("name", "id"))
        )
    elif target_label is not None:
        labels = [target_label]
    else:
        labels = []

    return [
        label.pk
        for label in sorted(
            labels,
            key=lambda item: (
                distribution.get(item.pk, {}).get("total", 0),
                item.name,
                item.pk,
            ),
        )
    ]


def _build_segment_frame_buckets(
    *,
    dataset: AIDataSet | None,
    label_set: LabelSet | None,
    only_prediction_segments: bool,
    require_extracted_frames: bool,
) -> dict[int, set[int]]:
    if dataset is None:
        return {}

    from endoreg_db.models import Frame

    buckets: dict[int, set[int]] = defaultdict(set)

    segments = (
        dataset.video_annotations.select_related("label", "source")
        .filter(
            label__isnull=False,
            video_file_id__isnull=False,
            start_frame_number__isnull=False,
            end_frame_number__isnull=False,
        )
        .order_by("video_file_id", "start_frame_number", "end_frame_number")
    )

    segments_by_video_id: dict[int, list[LabelVideoSegment]] = defaultdict(list)

    for segment in cast(Iterable[LabelVideoSegment], segments.iterator()):
        if only_prediction_segments and not is_prediction_segment(segment):
            continue
        segment_label_id = cast(int, getattr(segment, "label_id"))
        if not _label_allowed_by_set(segment_label_id, label_set):
            continue
        start_frame_number = cast(int, getattr(segment, "start_frame_number"))
        end_frame_number = cast(int, getattr(segment, "end_frame_number"))
        video_file_id = cast(int, getattr(segment, "video_file_id"))
        if start_frame_number >= end_frame_number:
            continue

        segments_by_video_id[video_file_id].append(segment)

    for video_id, video_segments in segments_by_video_id.items():
        min_start = min(
            cast(int, getattr(segment, "start_frame_number"))
            for segment in video_segments
        )
        max_end = max(
            cast(int, getattr(segment, "end_frame_number"))
            for segment in video_segments
        )

        frame_rows = Frame.objects.filter(
            video_id=video_id,
            frame_number__gte=min_start,
            frame_number__lt=max_end,
        )
        if require_extracted_frames:
            frame_rows = frame_rows.filter(is_extracted=True)
        frame_rows = frame_rows.values_list("id", "frame_number")

        frame_ids_by_number = {
            frame_number: frame_id for frame_id, frame_number in frame_rows
        }

        for segment in video_segments:
            for frame_number, frame_id in frame_ids_by_number.items():
                if (
                    segment.start_frame_number
                    <= frame_number
                    < segment.end_frame_number
                ):
                    buckets[cast(int, getattr(segment, "label_id"))].add(frame_id)

    return {label_id: frame_ids for label_id, frame_ids in buckets.items() if frame_ids}


def _build_annotation_frame_buckets(
    *,
    dataset: AIDataSet | None,
    label_set: LabelSet | None,
    require_extracted_frames: bool,
) -> dict[int, set[int]]:
    if dataset is None:
        return {}

    buckets: dict[int, set[int]] = defaultdict(set)
    annotations = dataset.image_annotations.select_related("label").filter(
        label__isnull=False,
        value=True,
        frame__isnull=False,
    )
    if require_extracted_frames:
        annotations = annotations.filter(frame__is_extracted=True)

    for annotation in cast(
        Iterable[ImageClassificationAnnotationLike], annotations.iterator()
    ):
        label_id = cast(int, getattr(annotation, "label_id"))
        frame_id = cast(int, getattr(annotation, "frame_id"))
        if not _label_allowed_by_set(label_id, label_set):
            continue
        buckets[label_id].add(frame_id)

    return {label_id: frame_ids for label_id, frame_ids in buckets.items() if frame_ids}


def _build_dataset_candidate_frame_ids(
    *,
    dataset: AIDataSet | None,
    label_set: LabelSet | None,
    only_prediction_segments: bool,
    require_extracted_frames: bool,
) -> set[int] | None:
    if dataset is None:
        return None

    frame_ids: set[int] = set()
    annotations = dataset.image_annotations.select_related("label").filter(
        label__isnull=False,
        frame__isnull=False,
    )
    if require_extracted_frames:
        annotations = annotations.filter(frame__is_extracted=True)
    for annotation in cast(
        Iterable[FrameAnnotationImageAnnotationLike], annotations.iterator()
    ):
        label_id = annotation.label_id
        frame_id = annotation.frame_id
        if _label_allowed_by_set(label_id, label_set):
            frame_ids.add(frame_id)

    segment_frame_buckets = _build_segment_frame_buckets(
        dataset=dataset,
        label_set=label_set,
        only_prediction_segments=only_prediction_segments,
        require_extracted_frames=require_extracted_frames,
    )
    for segment_frame_ids in segment_frame_buckets.values():
        frame_ids.update(segment_frame_ids)

    return frame_ids


def _merge_frame_buckets(*bucket_maps: dict[int, set[int]]) -> dict[int, set[int]]:
    merged: dict[int, set[int]] = defaultdict(set)
    for bucket_map in bucket_maps:
        for label_id, frame_ids in bucket_map.items():
            merged[label_id].update(frame_ids)
    return {label_id: frame_ids for label_id, frame_ids in merged.items() if frame_ids}


def _pick_balanced_dataset_frame(
    *,
    spec: FrameAnnotationQueueSpec,
    label_order: list[int],
    frame_buckets: dict[int, set[int]],
    exclude_frame_ids: set[int],
) -> tuple[FrameLike | None, int | None]:
    for label_id in label_order:
        bucket_frame_ids = frame_buckets.get(label_id)
        if not bucket_frame_ids:
            continue

        frame = _pick_random_frame(
            spec=spec,
            exclude_frame_ids=exclude_frame_ids,
            candidate_frame_ids=bucket_frame_ids,
        )
        if frame is not None:
            return frame, label_id

    return None, None


def serialize_frame_annotation(
    annotation: FrameAnnotationImageAnnotationLike,
) -> FrameAnnotationAnnotationPayload:
    return FrameAnnotationAnnotationPayload(
        id=cast(int, getattr(annotation, "pk", getattr(annotation, "id"))),
        label_id=annotation.label_id,
        label_name=cast(str, getattr(annotation.label, "name")),
        value=annotation.value,
        float_value=cast(float | None, getattr(annotation, "float_value", None)),
        annotator=annotation.annotator,
        information_source_name=cast(
            str | None, getattr(annotation.information_source, "name", None)
        ),
        model_meta_id=annotation.model_meta_id,
        external_annotation_id=annotation.external_annotation_id,
    )


def frame_manual_annotations(
    *,
    frame: FrameLike,
    label_set: LabelSet | None,
    information_source_name: str,
    annotator: str,
) -> QuerySet[ImageClassificationAnnotation]:
    queryset = frame.image_classification_annotations.select_related(
        "label", "information_source", "model_meta"
    ).filter(
        manual_annotation_filter(information_source_name)
        | manual_frame_annotation_preference_filter()
    )
    if label_set is not None:
        queryset = queryset.filter(label__label_sets=label_set)
    if annotator:
        queryset = queryset.filter(annotator=annotator)
    return cast(
        "QuerySet[ImageClassificationAnnotation]",
        queryset.order_by("label__name", "id").distinct(),
    )


def frame_prediction_annotations(
    *, frame: FrameLike, label_set: LabelSet | None
) -> QuerySet[ImageClassificationAnnotation]:
    queryset = frame.image_classification_annotations.select_related(
        "label", "information_source", "model_meta"
    ).filter(prediction_annotation_filter())
    if label_set is not None:
        queryset = queryset.filter(label__label_sets=label_set)
    return cast(
        "QuerySet[ImageClassificationAnnotation]",
        queryset.order_by("label__name", "id").distinct(),
    )


def serialize_frame_task(
    frame: FrameLike,
    *,
    spec: FrameAnnotationQueueSpec,
) -> FrameAnnotationTaskPayload:
    label_options: list[FrameAnnotationLabelOptionPayload] = []
    if spec.label_set is not None:
        label_options = [
            FrameAnnotationLabelOptionPayload(
                id=cast(int, getattr(label, "pk")),
                name=cast(str, getattr(label, "name")),
            )
            for label in cast(
                Iterable[LabelLike], spec.label_set.labels.all().order_by("name", "id")
            )
        ]
    elif spec.target_label is not None:
        label_options = [
            FrameAnnotationLabelOptionPayload(
                id=cast(int, getattr(spec.target_label, "pk")),
                name=cast(str, getattr(spec.target_label, "name")),
            )
        ]

    manual_annotations = cast(
        list[FrameAnnotationImageAnnotationLike],
        list(
            frame_manual_annotations(
                frame=frame,
                label_set=spec.label_set,
                information_source_name=resolve_frame_information_source_name(
                    spec.information_source_name
                ),
                annotator=spec.annotator,
            )
        ),
    )
    prediction_annotations = cast(
        list[FrameAnnotationImageAnnotationLike],
        list(frame_prediction_annotations(frame=frame, label_set=spec.label_set)),
    )

    manual_positive_ids = _preferred_manual_positive_label_ids(manual_annotations)
    prediction_positive_ids = [
        annotation.label_id for annotation in prediction_annotations if annotation.value
    ]

    return FrameAnnotationTaskPayload(
        frame_id=cast(int, getattr(frame, "pk", getattr(frame, "id"))),
        video_id=cast(int, getattr(frame, "video_id")),
        frame_number=cast(int, getattr(frame, "frame_number")),
        relative_path=cast(str, getattr(frame, "relative_path")),
        frame_stream_path=build_video_frame_stream_path(
            cast(int, getattr(frame, "video_id")),
            cast(int, getattr(frame, "frame_number")),
        ),
        annotation_mode="multilabel",
        label_options=label_options,
        manual_annotations=[
            serialize_frame_annotation(annotation) for annotation in manual_annotations
        ],
        prediction_annotations=[
            serialize_frame_annotation(annotation)
            for annotation in prediction_annotations
        ],
        manual_positive_label_ids=manual_positive_ids,
        prediction_positive_label_ids=prediction_positive_ids,
        suggested_label_ids=manual_positive_ids or prediction_positive_ids,
    )


def _preferred_manual_positive_label_ids(
    manual_annotations: list[FrameAnnotationImageAnnotationLike],
) -> list[int]:
    annotations_by_label_id: dict[int, list[FrameAnnotationImageAnnotationLike]] = {}
    for annotation in manual_annotations:
        annotations_by_label_id.setdefault(annotation.label_id, []).append(annotation)

    positive_label_ids: list[int] = []
    for label_id in sorted(annotations_by_label_id):
        label_annotations = annotations_by_label_id[label_id]
        preferred_annotations = [
            annotation
            for annotation in label_annotations
            if not is_segment_derived_external_annotation_id(
                annotation.external_annotation_id
            )
        ]
        selected_annotations = preferred_annotations or label_annotations
        if any(annotation.value for annotation in selected_annotations):
            positive_label_ids.append(label_id)
    return positive_label_ids


# Transitional service-facing aliases allow the workflow facade to own queue
# orchestration while its query helpers are migrated out of this legacy module.
build_dataset_target_buckets = _build_dataset_target_buckets
build_dataset_label_distribution = _build_dataset_label_distribution
build_balanced_label_order = _build_balanced_label_order
build_segment_frame_buckets = _build_segment_frame_buckets
build_annotation_frame_buckets = _build_annotation_frame_buckets
build_dataset_candidate_frame_ids = _build_dataset_candidate_frame_ids
merge_frame_buckets = _merge_frame_buckets
pick_balanced_dataset_frame = _pick_balanced_dataset_frame
pick_random_frame = _pick_random_frame


def _segment_annotation_filters(
    *,
    video: VideoFile,
    start_frame_number: int,
    end_frame_number: int,
    label: Label | None,
    information_source_id: int | None,
    model_meta_id: int | None,
) -> dict[str, object]:
    if label is None:
        return {}

    filters: dict[str, object] = {
        "frame__video": video,
        "frame__frame_number__gte": start_frame_number,
        "frame__frame_number__lt": end_frame_number,
        "label": label,
    }

    if information_source_id is None:
        filters["information_source__isnull"] = True
    else:
        filters["information_source_id"] = information_source_id

    if model_meta_id is None:
        filters["model_meta__isnull"] = True
    else:
        filters["model_meta_id"] = model_meta_id

    return filters


def delete_frame_annotations_for_segment(
    *,
    video: VideoFile,
    start_frame_number: int,
    end_frame_number: int,
    label: Label | None,
    information_source_id: int | None,
    model_meta_id: int | None,
) -> int:
    from endoreg_db.models import ImageClassificationAnnotation

    filters = _segment_annotation_filters(
        video=video,
        start_frame_number=start_frame_number,
        end_frame_number=end_frame_number,
        label=label,
        information_source_id=information_source_id,
        model_meta_id=model_meta_id,
    )
    if not filters:
        return 0
    deleted, _ = ImageClassificationAnnotation.objects.filter(**filters).delete()
    return deleted


def sync_frame_annotations_for_segment(
    *,
    segment: LabelVideoSegmentLike,
    old_snapshot: SegmentAnnotationSnapshot | None = None,
) -> None:
    from endoreg_db.models import ImageClassificationAnnotation

    if old_snapshot:
        delete_frame_annotations_for_segment(
            video=old_snapshot["video"],
            start_frame_number=old_snapshot["start_frame_number"],
            end_frame_number=old_snapshot["end_frame_number"],
            label=old_snapshot["label"],
            information_source_id=old_snapshot["information_source_id"],
            model_meta_id=old_snapshot["model_meta_id"],
        )

    if segment.label is None:
        return

    info_source_id = segment.source_id
    model_meta = segment.get_model_meta()
    model_meta_id = cast(int | None, getattr(model_meta, "pk", None))

    frames_queryset = segment.get_frames().only("id")

    existing_frame_ids = set(
        ImageClassificationAnnotation.objects.filter(
            frame_id__in=frames_queryset.values("id"),
            label=segment.label,
            information_source_id=info_source_id,
            model_meta_id=model_meta_id,
        ).values_list("frame_id", flat=True)
    )
    if not is_prediction_segment(segment):
        preferred_manual_frame_ids = set(
            ImageClassificationAnnotation.objects.filter(
                frame_id__in=frames_queryset.values("id"),
                label=segment.label,
            )
            .filter(manual_frame_annotation_preference_filter())
            .filter(Q(annotator__isnull=True) | Q(annotator__exact=""))
            .values_list("frame_id", flat=True)
        )
        existing_frame_ids.update(preferred_manual_frame_ids)

    annotations_to_create: list[ImageClassificationAnnotation] = []
    for frame in frames_queryset.exclude(id__in=existing_frame_ids).iterator():
        frame_pk = cast(int, getattr(frame, "pk"))
        annotations_to_create.append(
            ImageClassificationAnnotation(
                frame=frame,
                label=segment.label,
                value=True,
                information_source_id=info_source_id,
                model_meta_id=model_meta_id,
                external_annotation_id=segment_derived_external_annotation_id(
                    segment_id=segment.pk,
                    frame_id=frame_pk,
                    label_id=segment.label_id,
                    information_source_id=info_source_id,
                    model_meta_id=model_meta_id,
                ),
            )
        )

    if annotations_to_create:
        ImageClassificationAnnotation.objects.bulk_create(
            annotations_to_create, ignore_conflicts=True
        )

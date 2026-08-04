from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from django.db.models import QuerySet
from django.utils import timezone

from endoreg_db.schemas import (
    AIFrameFormatManifest,
    AIFrameFormatStrategy,
    AITrainingDatasetManifest,
    AITrainingLabel,
    AITrainingSample,
)

if TYPE_CHECKING:
    from endoreg_db.models.aidataset.aidataset import AIDataSet
    from endoreg_db.models.label.annotation.image_classification import (
        ImageClassificationAnnotation,
    )
    from endoreg_db.models.label.label_set import LabelSet


class _LabelSetRelation(Protocol):
    def values_list(self, *fields: str, flat: bool = False) -> Iterable[int]: ...


class _TrainingLabel(Protocol):
    pk: int | None
    name: str
    label_sets: _LabelSetRelation


class _TrainingLabelSet(Protocol):
    pk: int
    name: str
    version: int

    def get_labels_in_order(self) -> list[_TrainingLabel]: ...


class _TrainingInformationSource(Protocol):
    name: str


class _TrainingVideo(Protocol):
    pk: int
    uuid: object
    is_processed: bool

    def get_crop_template(self) -> list[int] | None: ...


class _TrainingFrame(Protocol):
    pk: int | None
    video: _TrainingVideo
    file_path: Path
    relative_path: str
    frame_number: int
    timestamp: float | None


class _TrainingImageAnnotation(Protocol):
    pk: int | None
    frame_id: int
    label_id: int
    value: bool
    frame: _TrainingFrame
    information_source: _TrainingInformationSource | None


@dataclass(frozen=True)
class _ManifestSamples:
    samples: list[AITrainingSample]
    frames: list[_TrainingFrame]
    crop_templates_by_video_uuid: dict[str, list[int] | None]


@dataclass(frozen=True)
class _FrameProvenance:
    frame_ids: list[int]
    frame_numbers: list[int]
    frame_numbers_by_video_uuid: dict[str, list[int]]
    source_video_kind_by_video_uuid: dict[str, str]


def infer_training_label_set_from_annotations(
    annotations_qs: QuerySet[ImageClassificationAnnotation],
) -> LabelSet:
    from endoreg_db.models.label.label import Label
    from endoreg_db.models.label.label_set import LabelSet

    label_ids = list(annotations_qs.values_list("label_id", flat=True).distinct())
    if not label_ids:
        raise ValueError("Cannot infer LabelSet: dataset has no frame labels.")

    labels = Label.objects.filter(pk__in=label_ids).prefetch_related("label_sets")
    labelset_id_sets = [_labelset_ids(cast(_TrainingLabel, label)) for label in labels]
    common_labelset_ids = _common_labelset_ids(labelset_id_sets)
    return LabelSet.objects.get(pk=next(iter(common_labelset_ids)))


def _labelset_ids(label: _TrainingLabel) -> set[int]:
    labelset_ids = set(label.label_sets.values_list("pk", flat=True))
    if not labelset_ids:
        raise ValueError(
            f"Cannot infer LabelSet: label id={label.pk} "
            f"name={label.name!r} is not attached to a LabelSet."
        )
    return labelset_ids


def _common_labelset_ids(labelset_id_sets: Sequence[set[int]]) -> set[int]:
    common_labelset_ids = set(labelset_id_sets[0])
    for labelset_ids in labelset_id_sets[1:]:
        common_labelset_ids &= labelset_ids
    if not common_labelset_ids:
        raise ValueError(
            "Cannot infer LabelSet: no common LabelSet contains all frame labels."
        )
    if len(common_labelset_ids) > 1:
        raise ValueError(
            "Cannot infer LabelSet: multiple common LabelSets found. "
            "Pass label_set explicitly."
        )
    return common_labelset_ids


def build_frame_format_manifest(
    *,
    frames: Sequence[object],
    check_frame_format: bool,
    crop_templates_by_video_uuid: dict[str, list[int] | None],
    preprocessing_strategy: AIFrameFormatStrategy,
    recommended_model_input_strategy: AIFrameFormatStrategy,
) -> AIFrameFormatManifest:
    notes = [
        "Current anonymization output preserves frame dimensions and blackens "
        "pixels outside the endoscope ROI.",
        "New model training should prefer crop_to_endoscope_roi when the "
        "consumer can handle cropped dimensions.",
    ]
    if not check_frame_format:
        return _unchecked_frame_format(
            crop_templates_by_video_uuid=crop_templates_by_video_uuid,
            preprocessing_strategy=preprocessing_strategy,
            recommended_model_input_strategy=recommended_model_input_strategy,
            notes=notes,
        )

    expected, checked_frame_count, errors = _inspect_frame_formats(frames)
    _raise_frame_format_errors(errors)
    if expected is None:
        raise ValueError("Frame format validation failed: no frames were inspected.")
    image_format, width, height, mode = expected
    return AIFrameFormatManifest(
        check_required=True,
        status="passed",
        checked_frame_count=checked_frame_count,
        expected_image_format=image_format,
        expected_width=width,
        expected_height=height,
        expected_mode=mode,
        preprocessing_strategy=preprocessing_strategy,
        recommended_model_input_strategy=recommended_model_input_strategy,
        crop_templates_by_video_uuid=crop_templates_by_video_uuid,
        notes=notes,
    )


def _unchecked_frame_format(
    *,
    crop_templates_by_video_uuid: dict[str, list[int] | None],
    preprocessing_strategy: AIFrameFormatStrategy,
    recommended_model_input_strategy: AIFrameFormatStrategy,
    notes: list[str],
) -> AIFrameFormatManifest:
    return AIFrameFormatManifest(
        check_required=True,
        status="not_checked",
        preprocessing_strategy=preprocessing_strategy,
        recommended_model_input_strategy=recommended_model_input_strategy,
        crop_templates_by_video_uuid=crop_templates_by_video_uuid,
        notes=notes,
    )


def _inspect_frame_formats(
    frames: Sequence[object],
) -> tuple[tuple[str, int, int, str] | None, int, list[str]]:
    expected: tuple[str, int, int, str] | None = None
    checked_frame_count = 0
    errors: list[str] = []
    for frame in frames:
        current, error = _inspect_frame_format(frame)
        expected, checked = _record_frame_inspection(
            frame,
            current=current,
            error=error,
            expected=expected,
            errors=errors,
        )
        checked_frame_count += int(checked)
    return expected, checked_frame_count, errors


def _record_frame_inspection(
    frame: object,
    *,
    current: tuple[str, int, int, str] | None,
    error: str | None,
    expected: tuple[str, int, int, str] | None,
    errors: list[str],
) -> tuple[tuple[str, int, int, str] | None, bool]:
    if error is not None:
        errors.append(error)
        return expected, False
    if current is None:
        return expected, False
    if expected is None:
        return current, True
    if current != expected:
        errors.append(_frame_format_mismatch(frame, expected, current))
    return expected, True


def _inspect_frame_format(
    frame: object,
) -> tuple[tuple[str, int, int, str] | None, str | None]:
    frame_id = getattr(frame, "pk", None)
    frame_number = getattr(frame, "frame_number", None)
    try:
        frame_path = cast(Path, getattr(frame, "file_path"))
    except Exception as error:
        return None, (
            f"frame_id={frame_id} frame_number={frame_number}: "
            f"could not resolve frame path ({error})"
        )
    if not frame_path.exists():
        return None, (
            f"frame_id={frame_id} frame_number={frame_number}: "
            f"frame file missing at {frame_path}"
        )
    return _read_frame_format(frame_path, frame_id=frame_id, frame_number=frame_number)


def _read_frame_format(
    frame_path: Path, *, frame_id: object, frame_number: object
) -> tuple[tuple[str, int, int, str] | None, str | None]:
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(frame_path) as image:
            image_format = (image.format or frame_path.suffix.lstrip(".")).upper()
            width, height = image.size
            return (image_format, int(width), int(height), image.mode), None
    except (OSError, UnidentifiedImageError) as error:
        return None, (
            f"frame_id={frame_id} frame_number={frame_number}: "
            f"could not inspect frame image ({error})"
        )


def _frame_format_mismatch(
    frame: object,
    expected: tuple[str, int, int, str],
    observed: tuple[str, int, int, str],
) -> str:
    return (
        f"frame_id={getattr(frame, 'pk', None)} "
        f"frame_number={getattr(frame, 'frame_number', None)}: "
        f"format mismatch expected={expected} observed={observed}"
    )


def _raise_frame_format_errors(errors: Sequence[str]) -> None:
    if not errors:
        return
    detail = "; ".join(errors[:5])
    if len(errors) > 5:
        detail = f"{detail}; {len(errors) - 5} more errors"
    raise ValueError(f"Frame format validation failed: {detail}")


def build_frame_multilabel_training_manifest(
    dataset: AIDataSet,
    *,
    label_set: LabelSet | None = None,
    treat_unlabeled_as_negative: bool = False,
    include_file_paths: bool = False,
    check_frame_format: bool = True,
    preprocessing_strategy: AIFrameFormatStrategy = "preserve_dimensions_black_mask",
    recommended_model_input_strategy: AIFrameFormatStrategy = "crop_to_endoscope_roi",
    information_source_names: Iterable[str] | None = None,
) -> AITrainingDatasetManifest:
    _validate_manifest_dataset(dataset)
    normalized_source_names = _normalize_source_names(information_source_names)
    annotations_qs = _manifest_annotations(dataset, normalized_source_names)
    resolved_label_set = cast(
        _TrainingLabelSet,
        label_set or infer_training_label_set_from_annotations(annotations_qs),
    )
    labels = _training_label_set_labels(resolved_label_set)
    label_id_to_index = _label_id_to_index(labels)
    annotations_qs = _filter_annotations_for_labels(
        annotations_qs, label_id_to_index, resolved_label_set
    )
    annotations_by_frame_id, frame_order = _group_annotations(annotations_qs)
    training_labels = _training_labels(labels, resolved_label_set)
    manifest_samples = _build_samples(
        frame_order,
        annotations_by_frame_id,
        label_id_to_index=label_id_to_index,
        training_labels=training_labels,
        treat_unlabeled_as_negative=treat_unlabeled_as_negative,
        include_file_paths=include_file_paths,
    )
    frame_format = build_frame_format_manifest(
        frames=manifest_samples.frames,
        check_frame_format=check_frame_format,
        crop_templates_by_video_uuid=manifest_samples.crop_templates_by_video_uuid,
        preprocessing_strategy=preprocessing_strategy,
        recommended_model_input_strategy=recommended_model_input_strategy,
    )
    frame_provenance = _frame_provenance(manifest_samples.frames)
    return AITrainingDatasetManifest(
        dataset_id=dataset.pk,
        name=dataset.name,
        description=dataset.description,
        labels=training_labels,
        samples=manifest_samples.samples,
        frame_format=frame_format,
        class_frequencies=_class_frequencies(
            manifest_samples.samples, len(training_labels)
        ),
        provenance=_manifest_provenance(
            dataset=dataset,
            label_set=resolved_label_set,
            normalized_source_names=normalized_source_names,
            frame_provenance=frame_provenance,
            treat_unlabeled_as_negative=treat_unlabeled_as_negative,
            include_file_paths=include_file_paths,
            check_frame_format=check_frame_format,
        ),
    )


def _validate_manifest_dataset(dataset: AIDataSet) -> None:
    if dataset.pk is None:
        raise ValueError("Cannot build a training manifest for an unsaved AIDataSet.")
    if dataset.dataset_type != dataset.DATASET_TYPE_IMAGE:
        raise ValueError(
            "frame multilabel training manifests require dataset_type='image'."
        )
    if dataset.ai_model_type != dataset.AI_MODEL_TYPE_IMAGE_MULTILABEL:
        raise ValueError(
            "frame multilabel training manifests require "
            f"ai_model_type={dataset.AI_MODEL_TYPE_IMAGE_MULTILABEL!r}."
        )


def _normalize_source_names(
    information_source_names: Iterable[str] | None,
) -> list[str] | None:
    if information_source_names is None:
        return None
    return [
        str(source_name).strip()
        for source_name in information_source_names
        if str(source_name).strip()
    ]


def _manifest_annotations(
    dataset: AIDataSet, normalized_source_names: list[str] | None
) -> QuerySet[ImageClassificationAnnotation]:
    annotations_qs = (
        dataset.image_annotations.select_related(
            "frame__video", "label", "information_source"
        )
        .filter(frame__isnull=False, frame__is_extracted=True)
        .order_by("frame__video_id", "frame__frame_number", "label__name", "pk")
    )
    if normalized_source_names:
        annotations_qs = annotations_qs.filter(
            information_source__name__in=normalized_source_names
        )
    if not annotations_qs.exists():
        raise ValueError(
            f"AIDataSet id={dataset.pk} has no extracted frame annotations."
        )
    return annotations_qs


def _training_label_set_labels(
    label_set: _TrainingLabelSet,
) -> list[_TrainingLabel]:
    labels = label_set.get_labels_in_order()
    if not labels:
        raise ValueError(
            f"LabelSet id={label_set.pk} name={label_set.name!r} has no labels."
        )
    return labels


def _label_id_to_index(labels: Sequence[_TrainingLabel]) -> dict[int, int]:
    return {
        int(label.pk): index
        for index, label in enumerate(labels)
        if label.pk is not None
    }


def _filter_annotations_for_labels(
    annotations_qs: QuerySet[ImageClassificationAnnotation],
    label_id_to_index: dict[int, int],
    label_set: _TrainingLabelSet,
) -> QuerySet[ImageClassificationAnnotation]:
    filtered_qs = annotations_qs.filter(label_id__in=label_id_to_index)
    if not filtered_qs.exists():
        raise ValueError(
            "AIDataSet has no extracted frame annotations for the selected "
            f"LabelSet id={label_set.pk}."
        )
    return filtered_qs


def _group_annotations(
    annotations_qs: QuerySet[ImageClassificationAnnotation],
) -> tuple[dict[int, list[_TrainingImageAnnotation]], list[int]]:
    annotations_by_frame_id: dict[int, list[_TrainingImageAnnotation]] = defaultdict(
        list
    )
    frame_order: list[int] = []
    for annotation in cast(
        Iterable[_TrainingImageAnnotation], annotations_qs.iterator()
    ):
        frame_id = int(annotation.frame_id)
        if frame_id not in annotations_by_frame_id:
            frame_order.append(frame_id)
        annotations_by_frame_id[frame_id].append(annotation)
    return annotations_by_frame_id, frame_order


def _training_labels(
    labels: Sequence[_TrainingLabel], label_set: _TrainingLabelSet
) -> list[AITrainingLabel]:
    return [
        AITrainingLabel(
            id=int(label.pk),
            name=label.name,
            index=index,
            labelset_name=label_set.name,
            labelset_version=label_set.version,
        )
        for index, label in enumerate(labels)
        if label.pk is not None
    ]


def _build_samples(
    frame_order: Sequence[int],
    annotations_by_frame_id: dict[int, list[_TrainingImageAnnotation]],
    *,
    label_id_to_index: dict[int, int],
    training_labels: Sequence[AITrainingLabel],
    treat_unlabeled_as_negative: bool,
    include_file_paths: bool,
) -> _ManifestSamples:
    samples: list[AITrainingSample] = []
    frames: list[_TrainingFrame] = []
    crop_templates: dict[str, list[int] | None] = {}
    for sample_index, frame_id in enumerate(frame_order):
        frame_annotations = annotations_by_frame_id[frame_id]
        frame = frame_annotations[0].frame
        frames.append(frame)
        samples.append(
            _build_sample(
                sample_index,
                frame_id,
                frame_annotations,
                label_id_to_index=label_id_to_index,
                training_labels=training_labels,
                treat_unlabeled_as_negative=treat_unlabeled_as_negative,
                include_file_paths=include_file_paths,
                crop_templates=crop_templates,
            )
        )
    return _ManifestSamples(samples, frames, crop_templates)


def _build_sample(
    sample_index: int,
    frame_id: int,
    frame_annotations: Sequence[_TrainingImageAnnotation],
    *,
    label_id_to_index: dict[int, int],
    training_labels: Sequence[AITrainingLabel],
    treat_unlabeled_as_negative: bool,
    include_file_paths: bool,
    crop_templates: dict[str, list[int] | None],
) -> AITrainingSample:
    frame = frame_annotations[0].frame
    video_uuid = str(frame.video.uuid)
    _cache_crop_template(frame.video, video_uuid, crop_templates)
    label_values = [0.0] * len(training_labels)
    label_mask = [
        int(treat_unlabeled_as_negative) for _training_label in training_labels
    ]
    annotation_ids_by_label: dict[str, list[int]] = {}
    source_names: set[str] = set()
    values_by_label_index = _annotations_by_label_index(
        frame_annotations, label_id_to_index
    )
    for label_index, label_annotations in values_by_label_index.items():
        _apply_label_annotations(
            frame_id,
            label_index,
            label_annotations,
            training_labels=training_labels,
            label_values=label_values,
            label_mask=label_mask,
            annotation_ids_by_label=annotation_ids_by_label,
            source_names=source_names,
        )
    return AITrainingSample(
        sample_index=sample_index,
        path=frame.file_path if include_file_paths else None,
        relative_path=frame.relative_path,
        labels=label_values,
        label_mask=label_mask,
        group_id=video_uuid,
        frame_id=frame.pk,
        video_id=frame.video.pk,
        video_uuid=video_uuid,
        frame_number=frame.frame_number,
        timestamp=frame.timestamp,
        metadata={
            "annotation_ids_by_label": annotation_ids_by_label,
            "information_source_names": sorted(source_names),
        },
    )


def _cache_crop_template(
    video: _TrainingVideo,
    video_uuid: str,
    crop_templates: dict[str, list[int] | None],
) -> None:
    if video_uuid in crop_templates:
        return
    try:
        crop_templates[video_uuid] = video.get_crop_template()
    except Exception:
        crop_templates[video_uuid] = None


def _annotations_by_label_index(
    frame_annotations: Sequence[_TrainingImageAnnotation],
    label_id_to_index: dict[int, int],
) -> dict[int, list[_TrainingImageAnnotation]]:
    values_by_label_index: dict[int, list[_TrainingImageAnnotation]] = defaultdict(list)
    for annotation in frame_annotations:
        label_index = label_id_to_index.get(annotation.label_id)
        if label_index is not None:
            values_by_label_index[label_index].append(annotation)
    return values_by_label_index


def _apply_label_annotations(
    frame_id: int,
    label_index: int,
    label_annotations: Sequence[_TrainingImageAnnotation],
    *,
    training_labels: Sequence[AITrainingLabel],
    label_values: list[float],
    label_mask: list[int],
    annotation_ids_by_label: dict[str, list[int]],
    source_names: set[str],
) -> None:
    label_name = training_labels[label_index].name
    value = _single_annotation_value(frame_id, label_name, label_annotations)
    label_values[label_index] = 1.0 if value else 0.0
    label_mask[label_index] = 1
    annotation_ids_by_label[label_name] = [
        int(annotation.pk)
        for annotation in label_annotations
        if annotation.pk is not None
    ]
    source_names.update(_annotation_source_names(label_annotations))


def _single_annotation_value(
    frame_id: int,
    label_name: str,
    label_annotations: Sequence[_TrainingImageAnnotation],
) -> bool:
    distinct_values = {bool(annotation.value) for annotation in label_annotations}
    if len(distinct_values) > 1:
        raise ValueError(
            "Conflicting annotations for "
            f"frame_id={frame_id} label={label_name!r}. "
            "Filter by information_source_names or resolve the "
            "annotation conflict before training."
        )
    return distinct_values.pop()


def _annotation_source_names(
    label_annotations: Sequence[_TrainingImageAnnotation],
) -> set[str]:
    return {
        annotation.information_source.name
        for annotation in label_annotations
        if annotation.information_source is not None
    }


def _frame_provenance(frames: Sequence[_TrainingFrame]) -> _FrameProvenance:
    frame_ids: list[int] = []
    frame_numbers: list[int] = []
    frame_numbers_by_video_uuid: dict[str, list[int]] = defaultdict(list)
    source_video_kind_by_video_uuid: dict[str, str] = {}
    for frame in frames:
        if frame.pk is not None:
            frame_ids.append(int(frame.pk))
        frame_numbers.append(int(frame.frame_number))
        video_uuid = str(frame.video.uuid)
        frame_numbers_by_video_uuid[video_uuid].append(int(frame.frame_number))
        source_video_kind_by_video_uuid[video_uuid] = _source_video_kind(frame.video)
    return _FrameProvenance(
        frame_ids,
        frame_numbers,
        dict(frame_numbers_by_video_uuid),
        source_video_kind_by_video_uuid,
    )


def _source_video_kind(video: _TrainingVideo) -> str:
    return (
        "processed"
        if getattr(video, "is_processed", False)
        else "extracted_frame_cache"
    )


def _class_frequencies(
    samples: Sequence[AITrainingSample], label_count: int
) -> list[float]:
    positive_counts = [0.0] * label_count
    known_counts = [0.0] * label_count
    for sample in samples:
        _add_sample_counts(sample, positive_counts, known_counts)
    return [
        positive_counts[index] / known_counts[index]
        if known_counts[index] > 0.0
        else 0.0
        for index in range(label_count)
    ]


def _add_sample_counts(
    sample: AITrainingSample,
    positive_counts: list[float],
    known_counts: list[float],
) -> None:
    for label_index, (value, mask) in enumerate(zip(sample.labels, sample.label_mask)):
        if mask:
            known_counts[label_index] += 1.0
            positive_counts[label_index] += float(value)


def _manifest_provenance(
    *,
    dataset: AIDataSet,
    label_set: _TrainingLabelSet,
    normalized_source_names: list[str] | None,
    frame_provenance: _FrameProvenance,
    treat_unlabeled_as_negative: bool,
    include_file_paths: bool,
    check_frame_format: bool,
) -> dict[str, object]:
    return {
        "source": "endoreg_db.AIDataSet",
        "dataset_id": dataset.pk,
        "labelset_id": label_set.pk,
        "labelset_name": label_set.name,
        "labelset_version": label_set.version,
        "treat_unlabeled_as_negative": treat_unlabeled_as_negative,
        "include_file_paths": include_file_paths,
        "check_frame_format": check_frame_format,
        "information_source_names": normalized_source_names,
        "frame_source_mode": "selected_frame_materialization",
        "source_video_kind": _aggregate_source_video_kind(
            frame_provenance.source_video_kind_by_video_uuid
        ),
        "source_video_kind_by_video_uuid": (
            frame_provenance.source_video_kind_by_video_uuid
        ),
        "frame_ids": frame_provenance.frame_ids,
        "frame_numbers": frame_provenance.frame_numbers,
        "frame_numbers_by_video_uuid": frame_provenance.frame_numbers_by_video_uuid,
        "materialization_timestamp": timezone.now().isoformat(),
    }


def _aggregate_source_video_kind(source_kinds: dict[str, str]) -> str:
    return (
        "processed"
        if set(source_kinds.values()) == {"processed"}
        else "mixed_or_frame_cache"
    )


__all__ = [
    "build_frame_format_manifest",
    "build_frame_multilabel_training_manifest",
    "infer_training_label_set_from_annotations",
]

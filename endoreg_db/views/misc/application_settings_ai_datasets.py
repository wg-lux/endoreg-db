from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Literal, Protocol, TypeVar, cast

from django.db import models, transaction
from django.db.models import QuerySet
from django.http import FileResponse
from lx_dtypes.models.contracts.ai_dataset import (
    AIModelType,
    AIDataSetAttachmentResultContract,
    AIDataSetAttachVideoContract,
    AIDataSetCreateContract,
    DatasetType,
)
from lx_dtypes.models.contracts.application_settings import (
    ApplicationSettingsDataSetEntryPayload,
)
from pydantic import ValidationError
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.request import Request
from rest_framework.response import Response

from endoreg_db.helpers.model_ids import model_pk
from endoreg_db.models.aidataset.aidataset import AIDataSet
from endoreg_db.models.label.annotation.image_classification import (
    ImageClassificationAnnotation,
)
from endoreg_db.models.label.label import Label
from endoreg_db.models.label.label_set import LabelSet
from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.application_settings.ai_dataset_export import (
    create_ai_dataset_export,
    prepare_ai_dataset_export_download,
)
from endoreg_db.services.aidataset_frame_buckets import (
    build_frame_bucket_distribution,
)
from endoreg_db.services.aidataset_training_manifests import (
    build_frame_multilabel_training_manifest,
)
from endoreg_db.utils.permissions import EnvironmentAwarePermission

AI_DATASET_FRAME_FORMAT_STRATEGIES = {
    "preserve_dimensions_black_mask",
    "crop_to_endoscope_roi",
}
AIDataSetFrameFormatStrategy = Literal[
    "preserve_dimensions_black_mask",
    "crop_to_endoscope_roi",
]
_BatchModel = TypeVar("_BatchModel", bound=models.Model)


class _AttachmentOptions(Protocol):
    video_id: int | None
    frame_annotation_ids: list[int]
    segment_ids: list[int]
    include_frame_annotations: bool
    include_video_annotations: bool
    include_all_annotations: bool
    information_source_names: list[str]


@dataclass(frozen=True, slots=True)
class _AttachmentRows:
    explicit_frame_annotations: list[ImageClassificationAnnotation]
    explicit_segments: list[LabelVideoSegment]
    video_frame_annotations: list[ImageClassificationAnnotation]
    video_segments: list[LabelVideoSegment]


@dataclass(slots=True)
class _AttachmentResult:
    frame_annotation_ids: set[int]
    segment_ids: set[int]
    frame_annotation_count: int = 0
    segment_count: int = 0


@dataclass(frozen=True, slots=True)
class _TrainingManifestOptions:
    label_set: LabelSet | None
    treat_unlabeled_as_negative: bool
    include_file_paths: bool
    check_frame_format: bool
    preprocessing_strategy: AIDataSetFrameFormatStrategy
    recommended_model_input_strategy: AIDataSetFrameFormatStrategy
    information_source_names: list[str] | None


def _request_payload(data: object) -> dict[str, Any]:
    return cast(dict[str, Any], data) if isinstance(data, dict) else {}


def _ai_dataset_name(dataset: AIDataSet) -> str:
    return cast(str | None, getattr(dataset, "name", None)) or ""


def _ai_dataset_type(dataset: AIDataSet) -> DatasetType:
    value = dataset.dataset_type
    if value not in ("image", "video"):
        raise ValueError(f"Unsupported AI dataset type: {value!r}.")
    return value


def _ai_dataset_model_type(dataset: AIDataSet) -> AIModelType:
    value = dataset.ai_model_type
    if value not in (
        "image_multilabel_classification",
        "phi_region_detector",
        "video_segment_classification",
    ):
        raise ValueError(f"Unsupported AI model type: {value!r}.")
    return value


def _ai_dataset_is_active(dataset: AIDataSet) -> bool:
    return cast(bool, getattr(dataset, "is_active"))


def _integer_param_error(field_name: str) -> Response:
    return Response(
        {"errors": {field_name: f"{field_name} must be an integer."}},
        status=status.HTTP_400_BAD_REQUEST,
    )


def _parse_optional_integer_param(
    raw_value: object,
    *,
    field_name: str,
) -> tuple[int | None, Response | None]:
    if raw_value in (None, ""):
        return None, None
    if isinstance(raw_value, bool) or not isinstance(
        raw_value, (str, bytes, bytearray, int)
    ):
        return None, _integer_param_error(field_name)
    try:
        return int(raw_value), None
    except ValueError:
        return None, _integer_param_error(field_name)


def _application_settings_ai_dataset_entry(
    dataset: AIDataSet,
    *,
    dataset_counts: Counter[str] | None = None,
) -> ApplicationSettingsDataSetEntryPayload:
    name = _ai_dataset_name(dataset)
    return ApplicationSettingsDataSetEntryPayload(
        id=model_pk(dataset),
        value=name,
        label=name,
        dataset_type=_ai_dataset_type(dataset),
        ai_model_type=_ai_dataset_model_type(dataset),
        is_active=_ai_dataset_is_active(dataset),
        name_count=(dataset_counts or Counter()).get(name, 1),
    )


def _application_settings_ai_dataset_entries() -> list[
    ApplicationSettingsDataSetEntryPayload
]:
    dataset_counts: Counter[str] = Counter(
        str(name)
        for name in AIDataSet.objects.exclude(name__exact="").values_list(
            "name",
            flat=True,
        )
        if name is not None
    )
    entries: list[ApplicationSettingsDataSetEntryPayload] = []
    for dataset in AIDataSet.objects.exclude(name__exact="").order_by(
        "name", "dataset_type", "pk"
    ):
        entries.append(
            _application_settings_ai_dataset_entry(
                dataset,
                dataset_counts=dataset_counts,
            )
        )
    return entries


def _application_settings_dataset_entry_data(
    payload: ApplicationSettingsDataSetEntryPayload,
) -> dict[str, Any]:
    return payload.model_dump(mode="python")


def _application_settings_dataset_entries_data() -> list[dict[str, Any]]:
    return [
        _application_settings_dataset_entry_data(entry)
        for entry in _application_settings_ai_dataset_entries()
    ]


def _parse_ai_dataset_create_options(
    payload: object,
) -> tuple[AIDataSetCreateContract | None, dict[str, str]]:
    try:
        return AIDataSetCreateContract.model_validate(payload), {}
    except ValidationError as exc:
        errors: dict[str, str] = {}
        for error in exc.errors(include_url=False):
            location = error["loc"]
            message = str(error["msg"]).removeprefix("Value error, ")
            if location:
                field_name = str(location[0])
            elif "ai_model_type" in message:
                field_name = "ai_model_type"
            else:
                field_name = "payload"
            errors.setdefault(field_name, message)
        return None, errors


def _create_ai_dataset(options: AIDataSetCreateContract) -> AIDataSet:
    with transaction.atomic():
        return AIDataSet.objects.create(
            name=options.name,
            description=options.description,
            dataset_type=options.dataset_type,
            ai_model_type=options.ai_model_type,
            is_active=options.is_active,
        )


def _create_ai_dataset_response(data: object) -> Response:
    options, errors = _parse_ai_dataset_create_options(data)
    if errors:
        return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)
    assert options is not None
    dataset = _create_ai_dataset(options)
    return Response(
        _application_settings_dataset_entry_data(
            _application_settings_ai_dataset_entry(
                dataset,
                dataset_counts=Counter(
                    {options.name: AIDataSet.objects.filter(name=options.name).count()}
                ),
            ),
        ),
        status=status.HTTP_201_CREATED,
    )


def _resolve_ai_dataset_param(param: object) -> AIDataSet | None:
    normalized = str(param or "").strip()
    if not normalized:
        return None
    if normalized.isdecimal():
        dataset = AIDataSet.objects.filter(pk=int(normalized)).first()
        if dataset is not None:
            return dataset
    return AIDataSet.objects.filter(name=normalized).order_by("pk").first()


def _resolve_label_set_for_distribution(
    raw_value: object,
) -> tuple[LabelSet | None, Response | None]:
    label_group_id, error = _parse_optional_integer_param(
        raw_value,
        field_name="label_group_id",
    )
    if error is not None:
        return None, error
    if label_group_id is None:
        return None, None

    label_set = LabelSet.objects.filter(pk=label_group_id).first()
    if label_set is None:
        return None, Response(
            {
                "errors": {
                    "label_group_id": f"Unknown label_group_id: {label_group_id}."
                }
            },
            status=status.HTTP_404_NOT_FOUND,
        )
    return label_set, None


def _target_labels(label_set: LabelSet | None) -> QuerySet[Label]:
    labels = Label.objects.all()
    if label_set is not None:
        return labels.filter(label_sets=label_set)
    return labels


def _resolve_target_label_id(
    labels: QuerySet[Label],
    raw_value: object,
) -> tuple[Label | None, Response | None]:
    target_label_id, error = _parse_optional_integer_param(
        raw_value,
        field_name="target_label_id",
    )
    if error is not None or target_label_id is None:
        return None, error
    label = labels.filter(pk=target_label_id).first()
    if label is not None:
        return label, None
    return None, Response(
        {"errors": {"target_label_id": f"Unknown target_label_id: {target_label_id}."}},
        status=status.HTTP_404_NOT_FOUND,
    )


def _resolve_target_label_name(
    labels: QuerySet[Label],
    raw_value: object,
) -> tuple[Label | None, Response | None]:
    target_label_name = str(raw_value or "").strip()
    if not target_label_name:
        return None, None
    label = labels.filter(name=target_label_name).first()
    if label is None:
        label = labels.filter(name__iexact=target_label_name).first()
    if label is not None:
        return label, None
    return None, Response(
        {"errors": {"target_label": f"Unknown target_label: {target_label_name}."}},
        status=status.HTTP_404_NOT_FOUND,
    )


def _resolve_target_label_for_distribution(
    *,
    label_set: LabelSet | None,
    target_label_id_raw: object,
    target_label_name_raw: object,
) -> tuple[Label | None, Response | None]:
    if target_label_id_raw not in {None, ""}:
        return _resolve_target_label_id(
            _target_labels(label_set),
            target_label_id_raw,
        )
    return _resolve_target_label_name(
        _target_labels(label_set),
        target_label_name_raw,
    )


def _payload_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return bool(value)


def _payload_bool_field(
    payload: dict[str, Any],
    field_name: str,
    *,
    default: bool,
) -> tuple[bool, Response | None]:
    raw_value = payload.get(field_name, default)
    if isinstance(raw_value, bool):
        return raw_value, None
    if isinstance(raw_value, str):
        normalized = raw_value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True, None
        if normalized in {"0", "false", "no", "off"}:
            return False, None
    return (
        default,
        Response(
            {"errors": {field_name: f"{field_name} must be a boolean."}},
            status=status.HTTP_400_BAD_REQUEST,
        ),
    )


def _payload_strategy_field(
    payload: dict[str, Any],
    field_name: str,
    *,
    default: AIDataSetFrameFormatStrategy,
) -> tuple[AIDataSetFrameFormatStrategy, Response | None]:
    raw_value = payload.get(field_name, default)
    if not isinstance(raw_value, str):
        return (
            default,
            Response(
                {"errors": {field_name: f"{field_name} must be a string."}},
                status=status.HTTP_400_BAD_REQUEST,
            ),
        )
    normalized = cast(AIDataSetFrameFormatStrategy, raw_value.strip() or default)
    if normalized not in AI_DATASET_FRAME_FORMAT_STRATEGIES:
        allowed = ", ".join(sorted(AI_DATASET_FRAME_FORMAT_STRATEGIES))
        return (
            default,
            Response(
                {"errors": {field_name: f"{field_name} must be one of: {allowed}."}},
                status=status.HTTP_400_BAD_REQUEST,
            ),
        )
    return normalized, None


def _information_source_names_error(message: str) -> Response:
    return Response(
        {"errors": {"information_source_names": message}},
        status=status.HTTP_400_BAD_REQUEST,
    )


def _comma_separated_information_source_names(value: str) -> list[str] | None:
    names = [name.strip() for name in value.split(",") if name.strip()]
    return names or None


def _listed_information_source_names(
    values: list[object],
) -> tuple[list[str] | None, Response | None]:
    normalized_names: list[str] = []
    for item in values:
        if not isinstance(item, str):
            return None, _information_source_names_error(
                "information_source_names entries must be strings."
            )
        stripped = item.strip()
        if stripped:
            normalized_names.append(stripped)
    return normalized_names or None, None


def _payload_information_source_names(
    raw_value: object,
) -> tuple[list[str] | None, Response | None]:
    if raw_value in (None, ""):
        return None, None
    if isinstance(raw_value, str):
        return _comma_separated_information_source_names(raw_value), None
    if not isinstance(raw_value, list):
        return None, _information_source_names_error(
            "information_source_names must be a string or list of strings."
        )
    return _listed_information_source_names(cast(list[object], raw_value))


def _attach_queryset_in_batches(
    add_objects: Callable[[list[_BatchModel]], int],
    queryset: QuerySet[_BatchModel],
    *,
    batch_size: int = 1000,
) -> int:
    attached_count = 0
    batch: list[_BatchModel] = []
    for item in queryset.order_by("pk").iterator(chunk_size=batch_size):
        batch.append(item)
        if len(batch) >= batch_size:
            add_objects(batch)
            attached_count += len(batch)
            batch = []
    if batch:
        add_objects(batch)
        attached_count += len(batch)
    return attached_count


def _attachment_validation_response(exc: ValidationError) -> Response:
    errors: dict[str, str] = {}
    for error in exc.errors(include_url=False):
        location = error["loc"]
        field_name = str(location[0]) if location else "include_all_annotations"
        message = str(error["msg"]).removeprefix("Value error, ")
        errors.setdefault(field_name, message)
    return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)


def _parse_attachment_request(
    payload: object,
) -> tuple[_AttachmentOptions | None, Response | None]:
    try:
        validated = AIDataSetAttachVideoContract.model_validate(payload)
        return cast(_AttachmentOptions, validated), None
    except ValidationError as exc:
        return None, _attachment_validation_response(exc)


def _load_explicit_frame_annotations(
    annotation_ids: list[int],
) -> tuple[list[ImageClassificationAnnotation], Response | None]:
    if not annotation_ids:
        return [], None
    annotations = ImageClassificationAnnotation.objects.filter(pk__in=annotation_ids)
    missing_ids = sorted(
        set(annotation_ids) - set(annotations.values_list("pk", flat=True))
    )
    if missing_ids:
        return [], Response(
            {"errors": {"frame_annotation_ids": f"Unknown IDs: {missing_ids}."}},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return list(annotations), None


def _load_explicit_segments(
    segment_ids: list[int],
) -> tuple[list[LabelVideoSegment], Response | None]:
    if not segment_ids:
        return [], None
    segments = LabelVideoSegment.objects.filter(pk__in=segment_ids)
    missing_ids = sorted(set(segment_ids) - set(segments.values_list("pk", flat=True)))
    if missing_ids:
        return [], Response(
            {"errors": {"segment_ids": f"Unknown IDs: {missing_ids}."}},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return list(segments), None


def _filter_frame_annotations_for_video(
    video_id: int,
    source_names: list[str] | None,
) -> list[ImageClassificationAnnotation]:
    annotations = ImageClassificationAnnotation.objects.filter(frame__video_id=video_id)
    if source_names:
        annotations = annotations.filter(information_source__name__in=source_names)
    return list(annotations)


def _filter_segments_for_video(
    video_id: int,
    source_names: list[str] | None,
) -> list[LabelVideoSegment]:
    segments = LabelVideoSegment.objects.filter(video_file_id=video_id)
    if source_names:
        segments = segments.filter(source__name__in=source_names)
    return list(segments)


def _load_video_attachment_rows(
    options: _AttachmentOptions,
) -> tuple[
    list[ImageClassificationAnnotation], list[LabelVideoSegment], Response | None
]:
    if options.video_id is None:
        return [], [], None
    if not VideoFile.objects.filter(pk=options.video_id).exists():
        return (
            [],
            [],
            Response(
                {"errors": {"video_id": "VideoFile not found."}},
                status=status.HTTP_404_NOT_FOUND,
            ),
        )
    frame_annotations = (
        _filter_frame_annotations_for_video(
            options.video_id,
            options.information_source_names,
        )
        if options.include_frame_annotations
        else []
    )
    segments = (
        _filter_segments_for_video(
            options.video_id,
            options.information_source_names,
        )
        if options.include_video_annotations
        else []
    )
    return frame_annotations, segments, None


def _load_attachment_rows(
    options: _AttachmentOptions,
) -> tuple[_AttachmentRows | None, Response | None]:
    explicit_frames, error = _load_explicit_frame_annotations(
        options.frame_annotation_ids
    )
    if error is not None:
        return None, error
    explicit_segments, error = _load_explicit_segments(options.segment_ids)
    if error is not None:
        return None, error
    video_frames, video_segments, error = _load_video_attachment_rows(options)
    if error is not None:
        return None, error
    return (
        _AttachmentRows(
            explicit_frame_annotations=explicit_frames,
            explicit_segments=explicit_segments,
            video_frame_annotations=video_frames,
            video_segments=video_segments,
        ),
        None,
    )


def _prepare_attachment(
    payload: object,
) -> tuple[
    _AttachmentOptions | None,
    _AttachmentRows | None,
    Response | None,
]:
    options, error = _parse_attachment_request(payload)
    if error is not None:
        return None, None, error
    assert options is not None
    rows, error = _load_attachment_rows(options)
    return options, rows, error


def _attach_all_annotations(
    dataset: AIDataSet,
    options: _AttachmentOptions,
    result: _AttachmentResult,
) -> None:
    if options.include_frame_annotations:
        annotations = ImageClassificationAnnotation.objects.all()
        if options.information_source_names:
            annotations = annotations.filter(
                information_source__name__in=options.information_source_names
            )
        result.frame_annotation_count += _attach_queryset_in_batches(
            dataset.add_frame_annotations,
            annotations,
        )
    if options.include_video_annotations:
        segments = LabelVideoSegment.objects.all()
        if options.information_source_names:
            segments = segments.filter(
                source__name__in=options.information_source_names
            )
        result.segment_count += _attach_queryset_in_batches(
            dataset.add_video_annotations,
            segments,
        )


def _attach_explicit_rows(
    dataset: AIDataSet,
    rows: _AttachmentRows,
    result: _AttachmentResult,
) -> None:
    if rows.explicit_frame_annotations:
        dataset.add_frame_annotations(rows.explicit_frame_annotations)
        result.frame_annotation_ids.update(
            annotation.pk for annotation in rows.explicit_frame_annotations
        )
        result.frame_annotation_count += len(rows.explicit_frame_annotations)
    if rows.explicit_segments:
        dataset.add_video_annotations(rows.explicit_segments)
        result.segment_ids.update(segment.pk for segment in rows.explicit_segments)
        result.segment_count += len(rows.explicit_segments)


def _attach_video_rows(
    dataset: AIDataSet,
    options: _AttachmentOptions,
    rows: _AttachmentRows,
    result: _AttachmentResult,
) -> None:
    if options.video_id is None:
        return
    result.frame_annotation_ids.update(
        annotation.pk for annotation in rows.video_frame_annotations
    )
    result.segment_ids.update(segment.pk for segment in rows.video_segments)
    result.frame_annotation_count += len(rows.video_frame_annotations)
    result.segment_count += len(rows.video_segments)
    dataset.attach_video(
        options.video_id,
        include_frame_annotations=options.include_frame_annotations,
        include_video_annotations=options.include_video_annotations,
        information_source_names=options.information_source_names,
    )


def _attach_dataset_rows(
    dataset: AIDataSet,
    options: _AttachmentOptions,
    rows: _AttachmentRows,
) -> _AttachmentResult:
    result = _AttachmentResult(frame_annotation_ids=set(), segment_ids=set())
    with transaction.atomic():
        if options.include_all_annotations:
            _attach_all_annotations(dataset, options, result)
        _attach_explicit_rows(dataset, rows, result)
        _attach_video_rows(dataset, options, rows, result)
    return result


def _resolve_manifest_label_set(
    payload: dict[str, Any],
) -> tuple[LabelSet | None, Response | None]:
    label_set_id, error = _parse_optional_integer_param(
        payload.get("label_set_id"),
        field_name="label_set_id",
    )
    if error is not None or label_set_id is None:
        return None, error
    label_set = LabelSet.objects.filter(pk=label_set_id).first()
    if label_set is not None:
        return label_set, None
    return None, Response(
        {"errors": {"label_set_id": f"Unknown label_set_id: {label_set_id}."}},
        status=status.HTTP_404_NOT_FOUND,
    )


def _parse_manifest_flags(
    payload: dict[str, Any],
) -> tuple[tuple[bool, bool, bool] | None, Response | None]:
    treat_unlabeled_as_negative, error = _payload_bool_field(
        payload,
        "treat_unlabeled_as_negative",
        default=False,
    )
    if error is not None:
        return None, error
    include_file_paths, error = _payload_bool_field(
        payload,
        "include_file_paths",
        default=False,
    )
    if error is not None:
        return None, error
    check_frame_format, error = _payload_bool_field(
        payload,
        "check_frame_format",
        default=True,
    )
    if error is not None:
        return None, error
    return (
        (treat_unlabeled_as_negative, include_file_paths, check_frame_format),
        None,
    )


def _parse_manifest_strategies(
    payload: dict[str, Any],
) -> tuple[
    tuple[AIDataSetFrameFormatStrategy, AIDataSetFrameFormatStrategy] | None,
    Response | None,
]:
    preprocessing_strategy, error = _payload_strategy_field(
        payload,
        "preprocessing_strategy",
        default="preserve_dimensions_black_mask",
    )
    if error is not None:
        return None, error
    recommended_model_input_strategy, error = _payload_strategy_field(
        payload,
        "recommended_model_input_strategy",
        default="crop_to_endoscope_roi",
    )
    if error is not None:
        return None, error
    return (
        (preprocessing_strategy, recommended_model_input_strategy),
        None,
    )


def _parse_training_manifest_options(
    payload: dict[str, Any],
) -> tuple[_TrainingManifestOptions | None, Response | None]:
    label_set, error = _resolve_manifest_label_set(payload)
    if error is not None:
        return None, error
    flags, error = _parse_manifest_flags(payload)
    if error is not None:
        return None, error
    strategies, error = _parse_manifest_strategies(payload)
    if error is not None:
        return None, error
    source_names, error = _payload_information_source_names(
        payload.get("information_source_names")
    )
    if error is not None:
        return None, error
    assert flags is not None
    assert strategies is not None
    return (
        _TrainingManifestOptions(
            label_set=label_set,
            treat_unlabeled_as_negative=flags[0],
            include_file_paths=flags[1],
            check_frame_format=flags[2],
            preprocessing_strategy=strategies[0],
            recommended_model_input_strategy=strategies[1],
            information_source_names=source_names,
        ),
        None,
    )


def _training_manifest_response(
    dataset: AIDataSet,
    options: _TrainingManifestOptions,
) -> Response:
    try:
        manifest = build_frame_multilabel_training_manifest(
            dataset,
            label_set=options.label_set,
            treat_unlabeled_as_negative=options.treat_unlabeled_as_negative,
            include_file_paths=options.include_file_paths,
            check_frame_format=options.check_frame_format,
            preprocessing_strategy=options.preprocessing_strategy,
            recommended_model_input_strategy=(options.recommended_model_input_strategy),
            information_source_names=options.information_source_names,
        )
    except ValueError as exc:
        return Response(
            {"errors": {"manifest": str(exc)}},
            status=status.HTTP_400_BAD_REQUEST,
        )

    return Response(
        {
            "dataset_id": dataset.pk,
            "dataset_name": _ai_dataset_name(dataset),
            "dataset_type": _ai_dataset_type(dataset),
            "ai_model_type": _ai_dataset_model_type(dataset),
            "config": {
                "label_set_id": (
                    options.label_set.pk if options.label_set is not None else None
                ),
                "treat_unlabeled_as_negative": options.treat_unlabeled_as_negative,
                "include_file_paths": options.include_file_paths,
                "check_frame_format": options.check_frame_format,
                "preprocessing_strategy": options.preprocessing_strategy,
                "recommended_model_input_strategy": (
                    options.recommended_model_input_strategy
                ),
                "information_source_names": options.information_source_names,
            },
            "summary": {
                "label_count": len(manifest.labels),
                "sample_count": len(manifest.samples),
                "class_frequencies": manifest.class_frequencies,
                "frame_format": manifest.frame_format.model_dump(mode="json"),
            },
            "manifest": manifest.model_dump(mode="json"),
            "lx_ai_core_manifest": manifest.to_lx_ai_core_dict(),
        },
        status=status.HTTP_200_OK,
    )


@api_view(["GET", "POST"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_ai_datasets_dropdown(request: Request) -> Response:
    if request.method == "POST":
        return _create_ai_dataset_response(request.data)
    return Response(
        _application_settings_dataset_entries_data(),
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_ai_dataset_attachments(
    request: Request, dataset_id: int
) -> Response:
    dataset = AIDataSet.objects.filter(pk=dataset_id).first()
    if dataset is None:
        return Response(
            {"errors": {"dataset_id": "AIDataSet not found."}},
            status=status.HTTP_404_NOT_FOUND,
        )

    options, rows, error = _prepare_attachment(request.data)
    if error is not None:
        return error
    assert options is not None
    assert rows is not None
    attached = _attach_dataset_rows(dataset, options, rows)

    response_payload = AIDataSetAttachmentResultContract.model_validate(
        {
            "dataset_id": dataset.pk,
            "video_id": options.video_id,
            "frame_annotation_count": dataset.image_annotations.count(),
            "video_annotation_count": dataset.video_annotations.count(),
            "attached_frame_annotation_ids": sorted(attached.frame_annotation_ids),
            "attached_segment_ids": sorted(attached.segment_ids),
            "attached_frame_annotation_count": attached.frame_annotation_count,
            "attached_segment_count": attached.segment_count,
        }
    ).model_dump(mode="json")
    return Response(
        response_payload,
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_ai_dataset_frame_bucket_distribution(
    request: Request, param: str
) -> Response:
    dataset = _resolve_ai_dataset_param(param)
    if dataset is None:
        return Response(
            {"detail": f"AIDataSet {param} was not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    label_set, error = _resolve_label_set_for_distribution(
        request.query_params.get(
            "label_group_id",
            request.query_params.get("label_set_id"),
        )
    )
    if error is not None:
        return error

    target_label, error = _resolve_target_label_for_distribution(
        label_set=label_set,
        target_label_id_raw=request.query_params.get("target_label_id"),
        target_label_name_raw=request.query_params.get("target_label"),
    )
    if error is not None:
        return error

    prediction_segments_only = _payload_bool(
        request.query_params.get("prediction_segments_only"),
        default=True,
    )
    distribution = build_frame_bucket_distribution(
        dataset,
        label_set=label_set,
        target_label=target_label,
        prediction_segments_only=prediction_segments_only,
    )
    return Response(distribution.model_dump(mode="json"), status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_ai_dataset_training_manifest(
    request: Request, param: str
) -> Response:
    dataset = _resolve_ai_dataset_param(param)
    if dataset is None:
        return Response(
            {"detail": f"AIDataSet {param} was not found."},
            status=status.HTTP_404_NOT_FOUND,
        )
    options, error = _parse_training_manifest_options(_request_payload(request.data))
    if error is not None:
        return error
    assert options is not None
    return _training_manifest_response(dataset, options)


@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_ai_dataset_export(request: Request) -> Response:
    result = create_ai_dataset_export(
        _request_payload(request.data),
        user=getattr(request, "user", None),
    )
    return Response(result.payload, status=result.status_code)


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_ai_dataset_export_download(
    request: Request, artifact_id: str
) -> Response | FileResponse:
    result = prepare_ai_dataset_export_download(artifact_id)
    if not result.is_file_response:
        return Response(result.payload, status=result.status_code)

    file_path = result.file_path
    if file_path is None:
        return Response(result.payload, status=result.status_code)

    response = FileResponse(
        file_path.open("rb"),
        as_attachment=True,
        filename=result.filename,
        content_type=result.content_type,
    )
    response["X-Content-SHA256"] = result.sha256
    response["X-Content-Length"] = str(result.byte_size)
    return response


__all__ = [
    "application_settings_ai_datasets_dropdown",
    "application_settings_ai_dataset_attachments",
    "application_settings_ai_dataset_frame_bucket_distribution",
    "application_settings_ai_dataset_training_manifest",
    "application_settings_ai_dataset_export",
    "application_settings_ai_dataset_export_download",
]

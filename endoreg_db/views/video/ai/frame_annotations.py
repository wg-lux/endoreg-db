from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeAlias, cast

from django.db import DatabaseError, IntegrityError, OperationalError, transaction
from django.utils import timezone
from lx_dtypes.models.contracts.frame_annotation import (
    FrameAnnotationQueueSpecPayload,
    FrameAnnotationRandomTaskResponsePayload,
    FrameAnnotationSkipResponsePayload,
)
from lx_dtypes.models.contracts.video_frame_annotations import (
    FrameAnnotationBulkItemData,
)
from pydantic import ValidationError
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from endoreg_db.helpers.model_ids import model_pk
from endoreg_db.schemas.frame_annotation_ingress import (
    validate_frame_annotation_bulk_ingress,
    validate_frame_annotation_skip_ingress,
)
from endoreg_db.models.aidataset.aidataset import AIDataSet
from endoreg_db.models.label.annotation.image_classification import (
    ImageClassificationAnnotation,
)
from endoreg_db.models.label.label import Label
from endoreg_db.models.label.label_set import LabelSet
from endoreg_db.models.media.frame.frame import Frame
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.metadata.model_meta import ModelMeta
from endoreg_db.models.other.information_source import InformationSource
from endoreg_db.services.frame_annotation_workflow import (
    DEFAULT_FRAME_INFORMATION_SOURCE_NAME,
    SUPPORTED_FRAME_SAMPLING_STRATEGIES,
    SUPPORTED_FRAME_TASK_MODES,
    FrameAnnotationQueueResult,
    FrameAnnotationTaskPayload,
    ai_dataset_requires_raw_frames,
    build_frame_task_queue,
    normalize_frame_sampling_strategy,
    normalize_frame_task_mode,
    resolve_ai_dataset_for_queue,
    resolve_frame_information_source_name,
)
from endoreg_db.services.annotation_access import (
    resolve_trusted_annotation_principal,
    validate_interactive_annotation_source,
)
from endoreg_db.serializers.label_video_segment.frame_annotation_bulk import (
    FrameAnnotationBulkItemSerializer,
)
from endoreg_db.services.frame_retention import (
    prune_unused_validated_outside_frames,
)
from endoreg_db.services.queue.frame_annotation_queue import (
    frame_annotation_queue_spec_from_payload,
)
from endoreg_db.services.video_files import VideoArtifactKind
from endoreg_db.utils.media_urls import build_video_frame_decoded_stream_path
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.authz.permissions import PolicyPermission

logger = logging.getLogger(__name__)

FRAME_FILE_TYPE_AUTO = "auto"
FrameAnnotationTask: TypeAlias = dict[str, Any]
FrameAnnotationResponse: TypeAlias = dict[str, Any]
FrameAnnotationKey: TypeAlias = tuple[int, int, int, str]
SUPPORTED_FRAME_FILE_TYPES = {
    FRAME_FILE_TYPE_AUTO,
    VideoArtifactKind.RAW.value,
    VideoArtifactKind.PROCESSED.value,
}


def resolve_request_annotator(
    request: Request,
    requested_annotator: str | None = None,
) -> str:
    return resolve_trusted_annotation_principal(request, requested_annotator)


def _coerce_int(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise ValueError(f"Expected integer-compatible value, got {type(value).__name__}.")


def _object_name(instance: object) -> str:
    return str(cast(object, getattr(instance, "name")))


def _information_source_id(source: InformationSource) -> int:
    return model_pk(source)


def _information_source_name(source: InformationSource) -> str:
    return _object_name(source)


def _frame_id(frame: Frame) -> int:
    return model_pk(frame)


def _frame_video_id(frame: Frame) -> int:
    return _coerce_int(cast(object, getattr(frame, "video_id")))


def _frame_video(frame: Frame) -> VideoFile:
    return cast(VideoFile, getattr(frame, "video"))


def _annotation_key(annotation: ImageClassificationAnnotation) -> FrameAnnotationKey:
    return (
        _coerce_int(cast(object, getattr(annotation, "frame_id"))),
        _coerce_int(cast(object, getattr(annotation, "label_id"))),
        _coerce_int(cast(object, getattr(annotation, "information_source_id"))),
        str(cast(object, getattr(annotation, "annotator"))),
    )


def _label_set_id(label_set: object) -> int:
    return model_pk(label_set)


def _label_name(label: object) -> str:
    return _object_name(label)


def _dataset_id(ai_dataset: object) -> int:
    return model_pk(ai_dataset)


def _dataset_name(ai_dataset: object) -> str:
    return str(cast(object, getattr(ai_dataset, "name")))


def _dataset_type(ai_dataset: object) -> str:
    return str(cast(object, getattr(ai_dataset, "dataset_type")))


def _dataset_model_type(ai_dataset: object) -> str:
    return str(cast(object, getattr(ai_dataset, "ai_model_type")))


def _dataset_frame_annotation_count(ai_dataset: AIDataSet) -> int:
    image_annotations = getattr(ai_dataset, "image_annotations")
    return int(image_annotations.count())


def _serializer_errors(serializer: object) -> object:
    return cast(object, getattr(serializer, "errors", {}))


def _resolve_bulk_ai_dataset(
    ai_dataset_id_raw: object,
) -> tuple[AIDataSet | None, Response | None]:
    if ai_dataset_id_raw in (None, ""):
        return None, None
    try:
        ai_dataset_id = _coerce_int(ai_dataset_id_raw)
    except (TypeError, ValueError):
        return None, Response(
            {"error": "ai_dataset_id must be an integer."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    ai_dataset = AIDataSet.objects.filter(pk=ai_dataset_id).first()
    if ai_dataset is not None:
        return ai_dataset, None
    return None, Response(
        {
            "error": "AIDataSet not found.",
            "details": {"ai_dataset_id": ai_dataset_id},
        },
        status=status.HTTP_404_NOT_FOUND,
    )


def _validate_bulk_annotation_items(
    annotation_items: Sequence[Mapping[str, Any]],
) -> tuple[list[FrameAnnotationBulkItemData] | None, Response | None]:
    serializer = FrameAnnotationBulkItemSerializer(
        data=[dict(item) for item in annotation_items],
        many=True,
    )
    if not serializer.is_valid():
        return None, Response(
            {"error": "Invalid data.", "details": _serializer_errors(serializer)},
            status=status.HTTP_400_BAD_REQUEST,
        )
    validated_items = cast(
        list[FrameAnnotationBulkItemData],
        serializer.validated_data,
    )
    if validated_items:
        return validated_items, None
    return None, Response(
        {"error": "At least one annotation is required."},
        status=status.HTTP_400_BAD_REQUEST,
    )


def _model_meta_ids(
    validated_items: Sequence[FrameAnnotationBulkItemData],
) -> set[int]:
    return {
        _coerce_int(model_meta_id)
        for item in validated_items
        if (model_meta_id := item.get("model_meta_id")) is not None
    }


def _frame_ids(
    validated_items: Sequence[FrameAnnotationBulkItemData],
) -> set[int]:
    return {_coerce_int(item["frame_id"]) for item in validated_items}


def _label_ids(
    validated_items: Sequence[FrameAnnotationBulkItemData],
) -> set[int]:
    return {_coerce_int(item["label_id"]) for item in validated_items}


def _source_names(
    validated_items: Sequence[FrameAnnotationBulkItemData],
) -> set[str]:
    return {str(item["information_source_name"]) for item in validated_items}


@dataclass(frozen=True)
class _BulkAnnotationReferences:
    frame_ids: set[int]
    label_ids: set[int]
    frame_video_by_id: dict[int, int]
    source_by_name: dict[str, InformationSource]


@dataclass(frozen=True, slots=True)
class _BulkWriteFailureDescriptor:
    code: str
    detail: str
    retryable: bool
    status_code: int


class _BulkRequestError(Exception):
    def __init__(self, response: Response) -> None:
        super().__init__("Invalid bulk frame annotation request.")
        self.response = response


def _raise_bulk_request_error(error: Response | None) -> None:
    if error is not None:
        raise _BulkRequestError(error)


def _frame_video_map(
    frame_ids: set[int],
) -> tuple[dict[int, int] | None, Response | None]:
    frame_rows = Frame.objects.filter(id__in=frame_ids).values("id", "video_id")
    frame_video_by_id = {
        _coerce_int(row["id"]): _coerce_int(row["video_id"])
        for row in cast(Iterable[Mapping[str, object]], frame_rows)
    }
    missing_frame_ids = sorted(frame_ids - set(frame_video_by_id))
    if not missing_frame_ids:
        return frame_video_by_id, None
    return None, Response(
        {
            "error": "Unknown frame_id values.",
            "details": {"missing_frame_ids": missing_frame_ids},
        },
        status=status.HTTP_400_BAD_REQUEST,
    )


def _validate_requested_video_frames(
    frame_video_by_id: Mapping[int, int],
    requested_video_id: int | None,
) -> Response | None:
    if requested_video_id is None:
        return None
    invalid_frame_ids = sorted(
        frame_id
        for frame_id, video_id in frame_video_by_id.items()
        if video_id != requested_video_id
    )
    if not invalid_frame_ids:
        return None
    return Response(
        {
            "error": "Some frame_id values do not belong to video_id.",
            "details": {
                "video_id": requested_video_id,
                "invalid_frame_ids": invalid_frame_ids,
            },
        },
        status=status.HTTP_400_BAD_REQUEST,
    )


def _validate_known_label_ids(label_ids: set[int]) -> Response | None:
    known_label_ids = {
        _coerce_int(label_id)
        for label_id in cast(
            Iterable[object],
            Label.objects.filter(id__in=label_ids).values_list("id", flat=True),
        )
    }
    missing_label_ids = sorted(label_ids - known_label_ids)
    if not missing_label_ids:
        return None
    return Response(
        {
            "error": "Unknown label_id values.",
            "details": {"missing_label_ids": missing_label_ids},
        },
        status=status.HTTP_400_BAD_REQUEST,
    )


def _validate_known_model_meta_ids(model_meta_ids: set[int]) -> Response | None:
    if not model_meta_ids:
        return None
    known_model_meta_ids = {
        _coerce_int(model_meta_id)
        for model_meta_id in cast(
            Iterable[object],
            ModelMeta.objects.filter(id__in=model_meta_ids).values_list(
                "id",
                flat=True,
            ),
        )
    }
    missing_model_meta_ids = sorted(model_meta_ids - known_model_meta_ids)
    if not missing_model_meta_ids:
        return None
    return Response(
        {
            "error": "Unknown model_meta_id values.",
            "details": {"missing_model_meta_ids": missing_model_meta_ids},
        },
        status=status.HTTP_400_BAD_REQUEST,
    )


def _resolve_information_sources(
    source_names: set[str],
) -> tuple[dict[str, InformationSource] | None, Response | None]:
    source_by_name = {
        _information_source_name(source): source
        for source in cast(
            Iterable[InformationSource],
            InformationSource.objects.filter(name__in=source_names),
        )
    }
    missing_source_names = sorted(source_names - set(source_by_name))
    if missing_source_names:
        return None, Response(
            {
                "error": "Unknown information_source_name values.",
                "details": {"missing_information_source_names": missing_source_names},
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    for source_name in source_names:
        validate_interactive_annotation_source(source_name)
    return source_by_name, None


def _resolve_bulk_annotation_references(
    validated_items: Sequence[FrameAnnotationBulkItemData],
    requested_video_id: int | None,
) -> tuple[_BulkAnnotationReferences | None, Response | None]:
    frame_ids = _frame_ids(validated_items)
    label_ids = _label_ids(validated_items)
    frame_video_by_id, error = _frame_video_map(frame_ids)
    _raise_bulk_request_error(error)
    assert frame_video_by_id is not None
    error = _validate_requested_video_frames(frame_video_by_id, requested_video_id)
    _raise_bulk_request_error(error)
    error = _validate_known_label_ids(label_ids)
    _raise_bulk_request_error(error)
    error = _validate_known_model_meta_ids(_model_meta_ids(validated_items))
    _raise_bulk_request_error(error)
    source_by_name, error = _resolve_information_sources(_source_names(validated_items))
    _raise_bulk_request_error(error)
    assert source_by_name is not None
    return (
        _BulkAnnotationReferences(
            frame_ids=frame_ids,
            label_ids=label_ids,
            frame_video_by_id=frame_video_by_id,
            source_by_name=source_by_name,
        ),
        None,
    )


def _build_annotations_to_upsert(
    validated_items: Sequence[FrameAnnotationBulkItemData],
    source_by_name: Mapping[str, InformationSource],
    request: Request,
) -> list[ImageClassificationAnnotation]:
    date_modified = timezone.now()
    annotations: list[ImageClassificationAnnotation] = []
    for item in validated_items:
        annotator = resolve_request_annotator(request, item.get("annotator"))
        annotations.append(
            ImageClassificationAnnotation(
                frame_id=item["frame_id"],
                label_id=item["label_id"],
                value=item.get("value", True),
                float_value=item.get("float_value"),
                information_source_id=_information_source_id(
                    source_by_name[item["information_source_name"]]
                ),
                annotator=annotator or "",
                model_meta_id=item.get("model_meta_id"),
                external_annotation_id=item.get("external_annotation_id"),
                date_modified=date_modified,
            )
        )
    return annotations


def _annotation_keys(
    annotations: Iterable[ImageClassificationAnnotation],
) -> set[FrameAnnotationKey]:
    return {_annotation_key(annotation) for annotation in annotations}


def _persisted_annotations_for_keys(
    *,
    references: _BulkAnnotationReferences,
    annotation_keys: set[FrameAnnotationKey],
) -> list[ImageClassificationAnnotation]:
    source_ids = {annotation_key[2] for annotation_key in annotation_keys}
    annotators = {annotation_key[3] for annotation_key in annotation_keys}
    candidates = ImageClassificationAnnotation.objects.filter(
        frame_id__in=references.frame_ids,
        label_id__in=references.label_ids,
        information_source_id__in=source_ids,
        annotator__in=annotators,
    )
    return [
        annotation
        for annotation in candidates
        if _annotation_key(annotation) in annotation_keys
    ]


def _persist_bulk_annotations(
    annotations_to_upsert: list[ImageClassificationAnnotation],
    references: _BulkAnnotationReferences,
    ai_dataset: AIDataSet | None,
) -> list[int]:
    ImageClassificationAnnotation.objects.bulk_create(
        annotations_to_upsert,
        update_conflicts=True,
        unique_fields=[
            "frame",
            "label",
            "information_source",
            "annotator",
        ],
        update_fields=[
            "value",
            "float_value",
            "model_meta",
            "external_annotation_id",
            "date_modified",
        ],
    )
    if ai_dataset is None:
        return []
    persisted_annotations = _persisted_annotations_for_keys(
        references=references,
        annotation_keys=_annotation_keys(annotations_to_upsert),
    )
    ai_dataset.add_frame_annotations(persisted_annotations)
    return [model_pk(annotation) for annotation in persisted_annotations]


def _target_video_id(
    requested_video_id: int | None,
    frame_video_by_id: Mapping[int, int],
) -> int | None:
    if requested_video_id is not None:
        return requested_video_id
    video_ids = set(frame_video_by_id.values())
    return next(iter(video_ids)) if len(video_ids) == 1 else None


def _bulk_upsert_success_response(
    *,
    annotations_to_upsert: Sequence[ImageClassificationAnnotation],
    references: _BulkAnnotationReferences,
    requested_video_id: int | None,
    ai_dataset: AIDataSet | None,
    attached_frame_annotation_ids: Sequence[int],
) -> Response:
    response_data: dict[str, Any] = {
        "status": "success",
        "upserted_count": len(annotations_to_upsert),
    }
    if ai_dataset is not None:
        response_data.update(
            {
                "ai_dataset_id": _dataset_id(ai_dataset),
                "attached_frame_annotation_ids": sorted(attached_frame_annotation_ids),
                "dataset_frame_annotation_count": _dataset_frame_annotation_count(
                    ai_dataset
                ),
            }
        )
    if requested_video_id is not None:
        response_data["video_id"] = requested_video_id
    target_video_id = _target_video_id(
        requested_video_id,
        references.frame_video_by_id,
    )
    target_video = (
        VideoFile.objects.filter(pk=target_video_id).first()
        if target_video_id is not None
        else None
    )
    if target_video is not None:
        response_data["pruned_unused_frames"] = prune_unused_validated_outside_frames(
            target_video
        )
    return Response(response_data, status=status.HTTP_200_OK)


def _bulk_write_failure_descriptor(
    error: DatabaseError,
) -> _BulkWriteFailureDescriptor:
    if isinstance(error, IntegrityError):
        return _BulkWriteFailureDescriptor(
            code="frame_annotation_write_conflict",
            detail="Frame annotations conflict with persisted data.",
            retryable=False,
            status_code=status.HTTP_409_CONFLICT,
        )
    if isinstance(error, OperationalError):
        return _BulkWriteFailureDescriptor(
            code="frame_annotation_write_temporarily_unavailable",
            detail="Frame annotation storage is temporarily unavailable.",
            retryable=True,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return _BulkWriteFailureDescriptor(
        code="frame_annotation_write_failed",
        detail="Frame annotations could not be saved.",
        retryable=False,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


def _bulk_write_failure_response(error: DatabaseError) -> Response:
    descriptor = _bulk_write_failure_descriptor(error)
    logger.error(
        "Bulk frame annotation write failed at the database boundary.",
        extra={
            "error_code": descriptor.code,
            "error_type": type(error).__name__,
            "retryable": descriptor.retryable,
            "write_committed": False,
        },
    )
    return Response(
        {
            "status": "error",
            "error": descriptor.detail,
            "code": descriptor.code,
            "retryable": descriptor.retryable,
            "write_committed": False,
        },
        status=descriptor.status_code,
    )


def _build_bulk_upsert_response(
    annotation_items: Sequence[Mapping[str, Any]],
    requested_video_id: int | None,
    request: Request,
    ai_dataset_id_raw: object = None,
) -> Response:
    try:
        ai_dataset, error = _resolve_bulk_ai_dataset(ai_dataset_id_raw)
        _raise_bulk_request_error(error)
        validated_items, error = _validate_bulk_annotation_items(annotation_items)
        _raise_bulk_request_error(error)
        assert validated_items is not None
        references, error = _resolve_bulk_annotation_references(
            validated_items,
            requested_video_id,
        )
        _raise_bulk_request_error(error)
        assert references is not None
    except _BulkRequestError as exc:
        return exc.response
    annotations_to_upsert = _build_annotations_to_upsert(
        validated_items,
        references.source_by_name,
        request,
    )

    try:
        with transaction.atomic():
            attached_frame_annotation_ids = _persist_bulk_annotations(
                annotations_to_upsert,
                references,
                ai_dataset,
            )
    except DatabaseError as exc:
        return _bulk_write_failure_response(exc)

    return _bulk_upsert_success_response(
        annotations_to_upsert=annotations_to_upsert,
        references=references,
        requested_video_id=requested_video_id,
        ai_dataset=ai_dataset,
        attached_frame_annotation_ids=attached_frame_annotation_ids,
    )


def _as_int(value: object, field_name: str) -> tuple[int | None, Response | None]:
    if value is None:
        return None, None
    try:
        return _coerce_int(value), None
    except (TypeError, ValueError):
        return None, Response(
            {"error": f"{field_name} must be an integer."},
            status=status.HTTP_400_BAD_REQUEST,
        )


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_frame_file_type(value: Any) -> tuple[str | None, Response | None]:
    if value is None or value == "":
        return None, None
    normalized = str(value).strip().lower()
    if normalized in SUPPORTED_FRAME_FILE_TYPES:
        return normalized, None
    return None, Response(
        {
            "error": "frame_file_type must be one of ['auto', 'raw', 'processed'].",
            "details": {"frame_file_type": value},
        },
        status=status.HTTP_400_BAD_REQUEST,
    )


def _video_has_artifact(video: VideoFile, artifact_kind: VideoArtifactKind) -> bool:
    if artifact_kind == VideoArtifactKind.PROCESSED:
        processed_file = getattr(video, "processed_file", None)
        return bool(processed_file and getattr(processed_file, "name", None))
    return bool(getattr(video, "has_raw", False))


def _resolve_task_artifact_kind(
    *,
    video: VideoFile | None,
    requested_frame_file_type: str | None,
) -> VideoArtifactKind | None:
    if video is None or requested_frame_file_type is None:
        return None
    if requested_frame_file_type == VideoArtifactKind.RAW.value:
        return (
            VideoArtifactKind.RAW
            if _video_has_artifact(video, VideoArtifactKind.RAW)
            else None
        )
    if requested_frame_file_type == VideoArtifactKind.PROCESSED.value:
        return (
            VideoArtifactKind.PROCESSED
            if _video_has_artifact(video, VideoArtifactKind.PROCESSED)
            else None
        )
    if _video_has_artifact(video, VideoArtifactKind.PROCESSED):
        return VideoArtifactKind.PROCESSED
    if _video_has_artifact(video, VideoArtifactKind.RAW):
        return VideoArtifactKind.RAW
    return None


def _attach_decoded_frame_stream_paths(
    tasks: list[FrameAnnotationTaskPayload],
    *,
    requested_frame_file_type: str | None,
) -> list[FrameAnnotationTaskPayload]:
    if requested_frame_file_type is None:
        return tasks

    video_ids = {int(task.video_id) for task in tasks}
    videos_by_id = VideoFile.objects.in_bulk(video_ids)

    stream_tasks: list[FrameAnnotationTaskPayload] = []
    for task in tasks:
        video_id = task.video_id
        frame_number = task.frame_number
        video_id_int = int(video_id)
        frame_number_int = int(frame_number)

        artifact_kind = _resolve_task_artifact_kind(
            video=videos_by_id.get(video_id_int),
            requested_frame_file_type=requested_frame_file_type,
        )
        if artifact_kind is None:
            continue

        task_with_stream = task.model_copy(
            update={
                "frame_file_type": artifact_kind.value,
                "decoded_frame_stream_path": build_video_frame_decoded_stream_path(
                    video_id_int,
                    frame_number_int,
                    file_type=artifact_kind.value,
                ),
            }
        )
        stream_tasks.append(task_with_stream)

    return stream_tasks


def _as_positive_int(
    value: Any, field_name: str, *, default: int
) -> tuple[int, Response | None]:
    if value is None or value == "":
        return default, None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default, Response(
            {"error": f"{field_name} must be an integer."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if parsed < 1:
        return default, Response(
            {"error": f"{field_name} must be >= 1."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return parsed, None


def _resolve_label_set_for_tasks(
    label_group_id_raw: Any,
) -> tuple[LabelSet | None, Response | None]:
    label_group_id, error = _as_int(label_group_id_raw, "label_group_id")
    if error is not None:
        return None, error
    if label_group_id is None:
        return None, None

    label_set = LabelSet.objects.filter(pk=label_group_id).first()
    if label_set is None:
        return None, Response(
            {
                "error": "Unknown label_group_id.",
                "details": {"label_group_id": label_group_id},
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    return label_set, None


def _resolve_label_for_tasks(
    *,
    label_name_raw: object,
    field_name: str,
    label_set: LabelSet | None,
) -> tuple[Label | None, Response | None]:
    if label_name_raw is None:
        return None, None

    label_name = str(label_name_raw).strip()
    if not label_name:
        return None, None

    label_qs = Label.objects.all()
    if label_set is not None:
        label_qs = label_qs.filter(label_sets=label_set)

    label = label_qs.filter(name=label_name).first()
    if label is None:
        label = label_qs.filter(name__iexact=label_name).first()
    if label is None:
        details: dict[str, Any] = {field_name: label_name}
        if label_set is not None:
            details["label_group_id"] = _label_set_id(label_set)
        return None, Response(
            {"error": f"Unknown {field_name}.", "details": details},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return label, None


class _TaskRequestError(Exception):
    def __init__(self, response: Response) -> None:
        super().__init__("Invalid frame annotation task request.")
        self.response = response


def _raise_task_request_error(error: Response | None) -> None:
    if error is not None:
        raise _TaskRequestError(error)


def _parse_task_mode(value: object) -> tuple[str | None, Response | None]:
    task_mode = str(value or "random").strip().lower()
    if task_mode in SUPPORTED_FRAME_TASK_MODES:
        return normalize_frame_task_mode(task_mode).value, None
    return None, Response(
        {
            "error": "task_mode must be one of ['random', 'filtered'].",
            "details": {"task_mode": task_mode},
        },
        status=status.HTTP_400_BAD_REQUEST,
    )


def _parse_sampling_strategy(value: object) -> tuple[str | None, Response | None]:
    strategy = str(value or "balanced").strip().lower()
    if strategy in SUPPORTED_FRAME_SAMPLING_STRATEGIES:
        return normalize_frame_sampling_strategy(strategy).value, None
    return None, Response(
        {
            "error": "dataset_frame_filter must be one of ['balanced', 'segments', 'annotations', 'none'].",
            "details": {"dataset_frame_filter": strategy},
        },
        status=status.HTTP_400_BAD_REQUEST,
    )


def _filter_label_query_value(request: Request) -> object:
    filter_label = request.query_params.get("filter_label")
    if filter_label is not None:
        return filter_label
    return request.query_params.get("previous_label")


def _validate_filtered_task_label(
    task_mode: str,
    filter_label: Label | None,
) -> None:
    if task_mode == "filtered" and filter_label is None:
        raise _TaskRequestError(
            Response(
                {
                    "error": "filter_label (or previous_label) is required when task_mode='filtered'."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        )


def _resolve_task_information_source(request: Request) -> str:
    information_source_name = resolve_frame_information_source_name(
        request.query_params.get(
            "information_source_name",
            request.query_params.get(
                "information_source",
                DEFAULT_FRAME_INFORMATION_SOURCE_NAME,
            ),
        )
    )
    try:
        return validate_interactive_annotation_source(information_source_name)
    except PermissionDenied as exc:
        raise _TaskRequestError(
            Response(
                {"error": str(exc.detail)},
                status=status.HTTP_403_FORBIDDEN,
            )
        ) from exc


def _resolve_task_ai_dataset(request: Request) -> AIDataSet | None:
    try:
        return resolve_ai_dataset_for_queue(
            dataset_id_raw=request.query_params.get("ai_dataset_id"),
            dataset_name_raw=request.query_params.get("ai_dataset_name"),
            dataset_type_raw=request.query_params.get("ai_dataset_type"),
        )
    except ValueError as exc:
        raise _TaskRequestError(
            Response(
                {"error": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        ) from exc


def _effective_frame_file_type(
    requested_frame_file_type: str | None,
    *,
    raw_video_required: bool,
) -> str | None:
    if raw_video_required and requested_frame_file_type is not None:
        return VideoArtifactKind.RAW.value
    return requested_frame_file_type


@dataclass(frozen=True)
class _FrameTaskRequestOptions:
    limit: int
    video_id: int | None
    label_set: LabelSet | None
    task_mode: str
    target_label: Label | None
    filter_label: Label | None
    information_source_name: str
    annotator: str
    exclude_annotated: bool
    sampling_strategy: str
    prediction_segments_only: bool
    ai_dataset: AIDataSet | None
    requested_frame_file_type: str | None
    raw_video_required: bool


def _parse_frame_task_request(request: Request) -> _FrameTaskRequestOptions:
    limit, error = _as_positive_int(
        request.query_params.get("limit"),
        "limit",
        default=1,
    )
    _raise_task_request_error(error)
    video_id, error = _as_int(request.query_params.get("video_id"), "video_id")
    _raise_task_request_error(error)
    label_set, error = _resolve_label_set_for_tasks(
        request.query_params.get("label_group_id")
    )
    _raise_task_request_error(error)
    task_mode, error = _parse_task_mode(request.query_params.get("task_mode", "random"))
    _raise_task_request_error(error)
    assert task_mode is not None
    target_label, error = _resolve_label_for_tasks(
        label_name_raw=request.query_params.get("target_label"),
        field_name="target_label",
        label_set=label_set,
    )
    _raise_task_request_error(error)
    filter_label, error = _resolve_label_for_tasks(
        label_name_raw=_filter_label_query_value(request),
        field_name="filter_label",
        label_set=label_set,
    )
    _raise_task_request_error(error)
    _validate_filtered_task_label(task_mode, filter_label)
    information_source_name = _resolve_task_information_source(request)
    annotator = resolve_request_annotator(
        request,
        request.query_params.get("annotator"),
    )
    exclude_annotated = _as_bool(
        request.query_params.get("exclude_annotated"),
        default=True,
    )
    sampling_strategy, error = _parse_sampling_strategy(
        request.query_params.get("dataset_frame_filter", "balanced")
    )
    _raise_task_request_error(error)
    assert sampling_strategy is not None
    prediction_segments_only = _as_bool(
        request.query_params.get("prediction_segments_only"),
        default=True,
    )
    ai_dataset = _resolve_task_ai_dataset(request)
    raw_video_required = ai_dataset_requires_raw_frames(ai_dataset)
    requested_frame_file_type, error = _parse_frame_file_type(
        request.query_params.get("frame_file_type")
    )
    _raise_task_request_error(error)
    return _FrameTaskRequestOptions(
        limit=limit,
        video_id=video_id,
        label_set=label_set,
        task_mode=task_mode,
        target_label=target_label,
        filter_label=filter_label,
        information_source_name=information_source_name,
        annotator=annotator,
        exclude_annotated=exclude_annotated,
        sampling_strategy=sampling_strategy,
        prediction_segments_only=prediction_segments_only,
        ai_dataset=ai_dataset,
        requested_frame_file_type=_effective_frame_file_type(
            requested_frame_file_type,
            raw_video_required=raw_video_required,
        ),
        raw_video_required=raw_video_required,
    )


def _optional_model_id(instance: object | None) -> int | None:
    return model_pk(instance) if instance is not None else None


def _build_task_queue_payload(
    options: _FrameTaskRequestOptions,
) -> FrameAnnotationQueueSpecPayload:
    frame_file_type = options.requested_frame_file_type
    return FrameAnnotationQueueSpecPayload(
        limit=options.limit,
        task_mode=options.task_mode,
        video_id=options.video_id,
        label_set_id=_optional_model_id(options.label_set),
        target_label_id=_optional_model_id(options.target_label),
        filter_label_id=_optional_model_id(options.filter_label),
        information_source_name=options.information_source_name,
        annotator=options.annotator,
        exclude_annotated=options.exclude_annotated,
        ai_dataset_id=_optional_model_id(options.ai_dataset),
        sampling_strategy=options.sampling_strategy,
        prediction_segments_only=options.prediction_segments_only,
        require_extracted_frames=frame_file_type is None,
        require_raw_video=frame_file_type == VideoArtifactKind.RAW.value,
        require_processed_video=frame_file_type == VideoArtifactKind.PROCESSED.value,
        require_streamable_video_artifact=frame_file_type == FRAME_FILE_TYPE_AUTO,
    )


def _add_task_label_details(
    details: dict[str, Any],
    options: _FrameTaskRequestOptions,
) -> None:
    if options.label_set is not None:
        details["label_group_id"] = _label_set_id(options.label_set)
    if options.target_label is not None:
        details["target_label"] = _label_name(options.target_label)
    if options.filter_label is not None:
        details["filter_label"] = _label_name(options.filter_label)


def _add_task_dataset_details(
    details: dict[str, Any],
    options: _FrameTaskRequestOptions,
) -> None:
    if options.ai_dataset is None:
        return
    details.update(
        {
            "ai_dataset_id": _dataset_id(options.ai_dataset),
            "ai_dataset_name": _dataset_name(options.ai_dataset),
            "ai_dataset_type": _dataset_type(options.ai_dataset),
            "ai_dataset_model_type": _dataset_model_type(options.ai_dataset),
            "raw_video_required": options.raw_video_required,
        }
    )


def _no_task_response(options: _FrameTaskRequestOptions) -> Response:
    details: dict[str, Any] = {
        "video_id": options.video_id,
        "information_source_name": options.information_source_name,
        "annotator": options.annotator,
        "exclude_annotated": options.exclude_annotated,
        "task_mode": options.task_mode,
        "limit": options.limit,
    }
    if options.requested_frame_file_type is not None:
        details["frame_file_type"] = options.requested_frame_file_type
    _add_task_label_details(details, options)
    _add_task_dataset_details(details, options)
    return Response(
        {
            "error": "No frame task available.",
            "details": details,
        },
        status=status.HTTP_404_NOT_FOUND,
    )


def _optional_name(instance: object | None) -> str | None:
    return _object_name(instance) if instance is not None else None


def _queue_dataset_metrics(
    queue_result: FrameAnnotationQueueResult,
    ai_dataset: AIDataSet | None,
) -> tuple[
    list[dict[str, int]],
    dict[str, int],
    dict[str, int],
    dict[str, int],
    dict[str, int],
]:
    if ai_dataset is None:
        return [], {}, {}, {}, {}
    return (
        queue_result.label_distribution,
        queue_result.selected_label_counts,
        queue_result.segment_bucket_counts,
        queue_result.annotation_bucket_counts,
        queue_result.bucket_counts,
    )


def _task_success_response(
    *,
    options: _FrameTaskRequestOptions,
    tasks: list[FrameAnnotationTaskPayload],
    queue_result: FrameAnnotationQueueResult,
) -> Response:
    (
        label_distribution,
        selected_label_counts,
        segment_bucket_counts,
        annotation_bucket_counts,
        bucket_counts,
    ) = _queue_dataset_metrics(queue_result, options.ai_dataset)
    response_payload = FrameAnnotationRandomTaskResponsePayload(
        task=tasks[0],
        tasks=tasks,
        count=len(tasks),
        task_mode=options.task_mode,
        selection_strategy=queue_result.selection_strategy,
        dataset_frame_filter=options.sampling_strategy,
        prediction_segments_only=options.prediction_segments_only,
        frame_file_type=options.requested_frame_file_type,
        label_group_id=_optional_model_id(options.label_set),
        target_label=_optional_name(options.target_label),
        filter_label=_optional_name(options.filter_label),
        ai_dataset_id=_optional_model_id(options.ai_dataset),
        ai_dataset_name=_optional_name(options.ai_dataset),
        ai_dataset_type=(
            _dataset_type(options.ai_dataset)
            if options.ai_dataset is not None
            else None
        ),
        label_distribution=label_distribution,
        selected_label_counts=selected_label_counts,
        segment_bucket_counts=segment_bucket_counts,
        annotation_bucket_counts=annotation_bucket_counts,
        bucket_counts=bucket_counts,
    )
    return Response(
        response_payload.to_response_dict(),
        status=status.HTTP_200_OK,
    )


class FrameAnnotationBulkUpsertView(APIView):
    """
    Bulk upsert endpoint for frame-level annotations.

    Accepted payload formats:
    1) List payload:
       [
         {frame_id, label_id, information_source_name, ...},
         ...
       ]
    2) Object payload:
       {
         "video_id": 123,  # optional safety check
         "annotations": [{...}, {...}]
       }
    """

    permission_classes = [EnvironmentAwarePermission, PolicyPermission]

    def post(
        self,
        request: Request,
        *args: object,
        **kwargs: object,
    ) -> Response:
        try:
            payload = validate_frame_annotation_bulk_ingress(cast(object, request.data))
        except (ValidationError, ValueError) as exc:
            return Response(
                {"error": "Invalid payload.", "details": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        annotation_items = [
            item.model_dump(mode="python", exclude_none=True)
            for item in payload.annotations
        ]

        return _build_bulk_upsert_response(
            annotation_items=cast(list[Mapping[str, Any]], annotation_items),
            requested_video_id=payload.video_id,
            request=request,
            ai_dataset_id_raw=payload.ai_dataset_id,
        )


class FrameAnnotationRandomTaskView(APIView):
    """
    Return one random frame task for annotation.
    """

    permission_classes = [EnvironmentAwarePermission, PolicyPermission]

    def get(
        self,
        request: Request,
        *args: object,
        **kwargs: object,
    ) -> Response:
        try:
            options = _parse_frame_task_request(request)
        except _TaskRequestError as exc:
            return exc.response
        queue_spec = frame_annotation_queue_spec_from_payload(
            _build_task_queue_payload(options)
        )
        queue_result = build_frame_task_queue(queue_spec)
        tasks = _attach_decoded_frame_stream_paths(
            queue_result.tasks,
            requested_frame_file_type=options.requested_frame_file_type,
        )
        if not tasks:
            return _no_task_response(options)
        return _task_success_response(
            options=options,
            tasks=tasks,
            queue_result=queue_result,
        )


class FrameAnnotationSkipView(APIView):
    """
    Acknowledge skipped frame tasks without creating annotations.
    """

    permission_classes = [EnvironmentAwarePermission, PolicyPermission]

    def post(
        self,
        request: Request,
        *args: object,
        **kwargs: object,
    ) -> Response:
        try:
            payload = validate_frame_annotation_skip_ingress(cast(object, request.data))
        except (ValidationError, ValueError) as exc:
            return Response(
                {"error": "Invalid payload.", "details": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        frame_id = payload.frame_id
        video_id = payload.video_id

        try:
            frame = Frame.objects.get(pk=frame_id)
        except Frame.DoesNotExist:
            return Response(
                {"error": "Unknown frame_id."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        frame_video_id = _frame_video_id(frame)
        if video_id is not None and frame_video_id != video_id:
            return Response(
                {
                    "error": "frame_id does not belong to video_id.",
                    "details": {"frame_id": frame_id, "video_id": video_id},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        requested_annotator = payload.annotator
        annotator = resolve_request_annotator(request, requested_annotator)
        reason = payload.reason.strip()

        information_source_name = resolve_frame_information_source_name(
            payload.information_source_name
            or payload.information_source
            or DEFAULT_FRAME_INFORMATION_SOURCE_NAME
        )
        information_source_name = validate_interactive_annotation_source(
            information_source_name
        )

        exclude_annotated = payload.exclude_annotated
        excluded_frame_ids: set[int] = {_frame_id(frame)}
        queue_spec_payload = FrameAnnotationQueueSpecPayload(
            limit=1,
            video_id=video_id if video_id is not None else frame_video_id,
            information_source_name=information_source_name,
            annotator=annotator,
            exclude_annotated=exclude_annotated,
            sampling_strategy="none",
            exclude_frame_ids=excluded_frame_ids,
        )
        queue_spec = frame_annotation_queue_spec_from_payload(queue_spec_payload)
        queue_result = build_frame_task_queue(queue_spec)

        logger.info(
            "Frame annotation skip: frame_id=%s video_id=%s annotator=%s reason=%s",
            _frame_id(frame),
            frame_video_id,
            annotator,
            reason,
        )

        response_payload = FrameAnnotationSkipResponsePayload(
            skipped_frame_id=_frame_id(frame),
            video_id=frame_video_id,
            annotator=annotator,
            reason=reason,
            next_task=queue_result.tasks[0] if queue_result.tasks else None,
            pruned_unused_frames=prune_unused_validated_outside_frames(
                _frame_video(frame)
            ),
        )

        return Response(response_payload.to_response_dict(), status=status.HTTP_200_OK)

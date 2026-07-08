from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable, Protocol, TypedDict, cast

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import ValidationError as DRFValidationError

from endoreg_db.models.aidataset.aidataset import AIDataSet
from endoreg_db.models.label.label import Label
from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
    suppress_label_video_segment_state_side_effects,
)
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.state.frame_annotation import (
    delete_frame_annotations_for_segment as default_delete_frame_annotations,
    sync_frame_annotations_for_segment as default_sync_frame_annotations,
)
from endoreg_db.models.state.video_segment_validation import (
    mark_segment_annotations_stale,
)
from endoreg_db.serializers.label_video_segment.label_video_segment import (
    LabelVideoSegmentSerializer,
    LabelVideoSegmentTimelineSerializer,
)
from endoreg_db.services.label_video_segment_states import (
    ensure_label_video_segment_states,
)
from endoreg_db.services.video_files import get_video_fps

SyncFrameAnnotations = Callable[..., None]
DeleteFrameAnnotations = Callable[..., int]


class BulkSegmentMutationServiceError(Exception):
    def __init__(self, response_data: Mapping[str, object], status_code: int) -> None:
        super().__init__(str(response_data.get("error", "Bulk mutation failed.")))
        self.response_data = dict(response_data)
        self.status_code = status_code


@dataclass(frozen=True)
class BulkSegmentMutationRequest:
    defer_annotation_sync: bool
    creates: list[object]
    updates: list[object]
    deletes: list[object]
    ai_dataset: AIDataSet | None


class _ModelMetaLike(Protocol):
    pk: int


class _BulkSegmentLike(Protocol):
    pk: int
    source_id: int | None
    video_file: VideoFile
    start_frame_number: int
    end_frame_number: int
    label: Label | None

    def get_model_meta(self) -> _ModelMetaLike | None: ...

    def delete(self) -> tuple[int, dict[str, int]]: ...


class _SegmentSnapshot(TypedDict):
    video: VideoFile
    start_frame_number: int
    end_frame_number: int
    label: object | None
    information_source_id: int | None
    model_meta_id: int | None


def bulk_mutate_video_segments(
    *,
    video: VideoFile,
    payload: Mapping[str, object],
    sync_frame_annotations: SyncFrameAnnotations = default_sync_frame_annotations,
    delete_frame_annotations_for_segment: DeleteFrameAnnotations = (
        default_delete_frame_annotations
    ),
) -> dict[str, object]:
    mutation_request = validate_bulk_segment_mutation_payload(payload)

    created_segments: list[dict[str, object]] = []
    created_segment_objects: list[LabelVideoSegment] = []
    updated_segments: list[LabelVideoSegment] = []
    deleted_segment_ids: list[int] = []
    attached_segment_ids: list[int] = []
    serializer_context = _bulk_serializer_context(video, mutation_request)

    try:
        with transaction.atomic(), suppress_label_video_segment_state_side_effects():
            _create_segments(
                video=video,
                creates=mutation_request.creates,
                defer_annotation_sync=mutation_request.defer_annotation_sync,
                created_segments=created_segments,
                created_segment_objects=created_segment_objects,
                sync_frame_annotations=sync_frame_annotations,
                serializer_context=serializer_context,
            )
            _update_segments(
                video=video,
                updates=mutation_request.updates,
                defer_annotation_sync=mutation_request.defer_annotation_sync,
                updated_segments=updated_segments,
                sync_frame_annotations=sync_frame_annotations,
                delete_frame_annotations_for_segment=(
                    delete_frame_annotations_for_segment
                ),
                serializer_context=serializer_context,
            )
            _delete_segments(
                video=video,
                deletes=mutation_request.deletes,
                deleted_segment_ids=deleted_segment_ids,
                delete_frame_annotations_for_segment=(
                    delete_frame_annotations_for_segment
                ),
            )

            ensure_label_video_segment_states(
                [*created_segment_objects, *updated_segments],
            )

            if mutation_request.defer_annotation_sync:
                mark_segment_annotations_stale(video)

            if mutation_request.ai_dataset is not None:
                segments_to_attach = [*created_segment_objects, *updated_segments]
                mutation_request.ai_dataset.add_video_annotations(segments_to_attach)
                attached_segment_ids = [segment.pk for segment in segments_to_attach]

    except DRFValidationError as exc:
        raise _invalid_bulk_payload(exc.detail) from exc

    return _bulk_mutation_response_payload(
        mutation_request=mutation_request,
        created_segments=created_segments,
        updated_segments=updated_segments,
        deleted_segment_ids=deleted_segment_ids,
        attached_segment_ids=attached_segment_ids,
    )


def validate_bulk_segment_mutation_payload(
    payload: Mapping[str, object],
) -> BulkSegmentMutationRequest:
    try:
        creates = _require_bulk_list(payload, "creates")
        updates = _require_bulk_list(payload, "updates")
        deletes = _require_bulk_list(payload, "deletes")
    except DRFValidationError as exc:
        raise _invalid_bulk_payload(exc.detail) from exc

    ai_dataset = _resolve_optional_ai_dataset(payload)
    if not creates and not updates and not deletes:
        raise BulkSegmentMutationServiceError(
            {"error": "At least one create, update, or delete is required."},
            status.HTTP_400_BAD_REQUEST,
        )

    return BulkSegmentMutationRequest(
        defer_annotation_sync=_query_param_as_bool(
            payload.get("defer_annotation_sync"),
            default=True,
        ),
        creates=creates,
        updates=updates,
        deletes=deletes,
        ai_dataset=ai_dataset,
    )


def _invalid_bulk_payload(detail: object) -> BulkSegmentMutationServiceError:
    return BulkSegmentMutationServiceError(
        {"error": "Invalid bulk segment payload", "details": detail},
        status.HTTP_400_BAD_REQUEST,
    )


def _bulk_serializer_context(
    video: VideoFile,
    mutation_request: BulkSegmentMutationRequest,
) -> dict[str, object]:
    context: dict[str, object] = {"video_file": video, "video_id": video.pk}
    if _mutation_request_uses_time_values(mutation_request):
        context["video_fps"] = get_video_fps(video)
    return context


def _mutation_request_uses_time_values(
    mutation_request: BulkSegmentMutationRequest,
) -> bool:
    for item in [*mutation_request.creates, *mutation_request.updates]:
        if isinstance(item, Mapping) and ("start_time" in item or "end_time" in item):
            return True
    return False


def _create_segments(
    *,
    video: VideoFile,
    creates: list[object],
    defer_annotation_sync: bool,
    created_segments: list[dict[str, object]],
    created_segment_objects: list[LabelVideoSegment],
    sync_frame_annotations: SyncFrameAnnotations,
    serializer_context: dict[str, object],
) -> None:
    for index, item in enumerate(creates):
        if not isinstance(item, Mapping):
            raise _bulk_item_validation_error(
                "creates",
                index,
                "Each create entry must be an object.",
            )

        item_map = cast(Mapping[str, object], item)
        client_id = item_map.get("client_id")
        segment_payload: dict[str, object] = dict(item_map)
        segment_payload.pop("client_id", None)
        segment_payload["video_id"] = video.pk

        serializer = LabelVideoSegmentSerializer(
            data=segment_payload,
            context=serializer_context,
        )
        if not serializer.is_valid():
            validation_errors = getattr(serializer, "errors")
            raise _bulk_item_validation_error(
                "creates",
                index,
                validation_errors,
            )

        segment = serializer.save()
        if not defer_annotation_sync:
            sync_frame_annotations(segment=segment)
        created_segment_objects.append(segment)
        created_segments.append(
            {
                "client_id": client_id,
                "segment": _timeline_segment_data(segment),
            }
        )


def _update_segments(
    *,
    video: VideoFile,
    updates: list[object],
    defer_annotation_sync: bool,
    updated_segments: list[LabelVideoSegment],
    sync_frame_annotations: SyncFrameAnnotations,
    delete_frame_annotations_for_segment: DeleteFrameAnnotations,
    serializer_context: dict[str, object],
) -> None:
    for index, item in enumerate(updates):
        if not isinstance(item, Mapping):
            raise _bulk_item_validation_error(
                "updates",
                index,
                "Each update entry must be an object.",
            )
        item_map = cast(Mapping[str, object], item)
        if "id" not in item_map:
            raise _bulk_item_validation_error(
                "updates",
                index,
                {"id": "This field is required."},
            )

        try:
            segment_id_value = item_map["id"]
            if not isinstance(segment_id_value, (int, float, str, bytes, bytearray)):
                raise TypeError
            segment_id = int(segment_id_value)
        except (TypeError, ValueError):
            raise _bulk_item_validation_error(
                "updates",
                index,
                {"id": "Must be an integer."},
            )

        segment = cast(
            _BulkSegmentLike,
            get_object_or_404(
                LabelVideoSegment.objects.select_related(
                    "video_file", "label", "source"
                ),
                id=segment_id,
                video_file=video,
            ),
        )
        old_snapshot = _segment_snapshot(segment)

        segment_payload: dict[str, object] = dict(item_map)
        segment_payload.pop("id", None)
        serializer = LabelVideoSegmentSerializer(
            cast(LabelVideoSegment, segment),
            data=segment_payload,
            partial=True,
            context=serializer_context,
        )
        if not serializer.is_valid():
            validation_errors = getattr(serializer, "errors")
            raise _bulk_item_validation_error(
                "updates",
                index,
                validation_errors,
            )

        updated_segment = serializer.save()
        if defer_annotation_sync:
            delete_frame_annotations_for_segment(
                video=old_snapshot["video"],
                start_frame_number=old_snapshot["start_frame_number"],
                end_frame_number=old_snapshot["end_frame_number"],
                label=old_snapshot["label"],
                information_source_id=old_snapshot["information_source_id"],
                model_meta_id=old_snapshot["model_meta_id"],
            )
        else:
            sync_frame_annotations(
                segment=updated_segment,
                old_snapshot=old_snapshot,
            )
        updated_segments.append(updated_segment)


def _delete_segments(
    *,
    video: VideoFile,
    deletes: list[object],
    deleted_segment_ids: list[int],
    delete_frame_annotations_for_segment: DeleteFrameAnnotations,
) -> None:
    for index, item in enumerate(deletes):
        segment_id = _bulk_delete_segment_id(item, index)
        segment = cast(
            _BulkSegmentLike,
            get_object_or_404(
                LabelVideoSegment.objects.select_related(
                    "video_file", "label", "source"
                ),
                id=segment_id,
                video_file=video,
            ),
        )
        if segment.label is not None:
            delete_model_meta = segment.get_model_meta()
            delete_frame_annotations_for_segment(
                video=segment.video_file,
                start_frame_number=segment.start_frame_number,
                end_frame_number=segment.end_frame_number,
                label=segment.label,
                information_source_id=segment.source_id,
                model_meta_id=delete_model_meta.pk if delete_model_meta else None,
            )
        segment.delete()
        deleted_segment_ids.append(segment_id)


def _bulk_mutation_response_payload(
    *,
    mutation_request: BulkSegmentMutationRequest,
    created_segments: list[dict[str, object]],
    updated_segments: list[LabelVideoSegment],
    deleted_segment_ids: list[int],
    attached_segment_ids: list[int],
) -> dict[str, object]:
    ai_dataset = mutation_request.ai_dataset
    return {
        "created": created_segments,
        "updated": cast(
            list[dict[str, object]],
            getattr(
                LabelVideoSegmentTimelineSerializer(
                    updated_segments,
                    many=True,
                ),
                "data",
            ),
        ),
        "deleted": deleted_segment_ids,
        "created_count": len(created_segments),
        "updated_count": len(updated_segments),
        "deleted_count": len(deleted_segment_ids),
        "defer_annotation_sync": mutation_request.defer_annotation_sync,
        "ai_dataset_id": ai_dataset.pk if ai_dataset is not None else None,
        "attached_segment_ids": sorted(attached_segment_ids),
        "dataset_video_annotation_count": (
            ai_dataset.video_annotations.count() if ai_dataset is not None else 0
        ),
    }


def _resolve_optional_ai_dataset(payload: Mapping[str, object]) -> AIDataSet | None:
    raw_value: object | None = payload.get("ai_dataset_id")
    if raw_value in (None, ""):
        return None
    try:
        dataset_id = int(str(raw_value).strip())
    except (TypeError, ValueError) as exc:
        raise BulkSegmentMutationServiceError(
            {"error": "ai_dataset_id must be an integer."},
            status.HTTP_400_BAD_REQUEST,
        ) from exc

    dataset = AIDataSet.objects.filter(pk=dataset_id).first()
    if dataset is None:
        raise BulkSegmentMutationServiceError(
            {
                "error": "AIDataSet not found.",
                "details": {"ai_dataset_id": dataset_id},
            },
            status.HTTP_404_NOT_FOUND,
        )
    return dataset


def _require_bulk_list(data: Mapping[str, object], field_name: str) -> list[object]:
    value: object | None = data.get(field_name, [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise DRFValidationError({field_name: "Must be a list."})
    return cast(list[object], value)


def _bulk_delete_segment_id(item: object, index: int) -> int:
    if isinstance(item, Mapping):
        raw_id: object | None = cast(Mapping[str, object], item).get("id")
    else:
        raw_id = item
    if raw_id is None:
        raise _bulk_item_validation_error(
            "deletes",
            index,
            "Each delete entry must be a segment id.",
        )
    try:
        if not isinstance(raw_id, (int, float, str, bytes, bytearray)):
            raise TypeError
        segment_id = int(raw_id)
    except (TypeError, ValueError):
        raise _bulk_item_validation_error(
            "deletes",
            index,
            "Each delete entry must be a segment id.",
        )
    if segment_id <= 0:
        raise _bulk_item_validation_error(
            "deletes",
            index,
            "Segment id must be a positive integer.",
        )
    return segment_id


def _bulk_item_validation_error(
    field_name: str,
    index: int,
    detail: object,
) -> DRFValidationError:
    indexed_errors: dict[str, object] = {str(index): detail}
    bulk_errors: dict[str, dict[str, object]] = {field_name: indexed_errors}
    return DRFValidationError(cast(Any, bulk_errors))


def _query_param_as_bool(value: object, *, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _segment_snapshot(segment: _BulkSegmentLike) -> _SegmentSnapshot:
    model_meta = segment.get_model_meta()
    return {
        "video": segment.video_file,
        "start_frame_number": segment.start_frame_number,
        "end_frame_number": segment.end_frame_number,
        "label": segment.label,
        "information_source_id": segment.source_id,
        "model_meta_id": model_meta.pk if model_meta else None,
    }


def _timeline_segment_data(segment: LabelVideoSegment) -> dict[str, object]:
    return cast(
        dict[str, object],
        getattr(LabelVideoSegmentTimelineSerializer(segment), "data"),
    )

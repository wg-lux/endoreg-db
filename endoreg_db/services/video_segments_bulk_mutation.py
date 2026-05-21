from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import ValidationError as DRFValidationError

from endoreg_db.models import AIDataSet, LabelVideoSegment, VideoFile
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

SyncFrameAnnotations = Callable[..., None]
DeleteFrameAnnotations = Callable[..., int]


class BulkSegmentMutationServiceError(Exception):
    def __init__(self, response_data: Mapping[str, Any], status_code: int) -> None:
        super().__init__(str(response_data.get("error", "Bulk mutation failed.")))
        self.response_data = dict(response_data)
        self.status_code = status_code


@dataclass(frozen=True)
class BulkSegmentMutationRequest:
    defer_annotation_sync: bool
    creates: list[Any]
    updates: list[Any]
    deletes: list[Any]
    ai_dataset: AIDataSet | None


def bulk_mutate_video_segments(
    *,
    video: VideoFile,
    payload: Mapping[str, Any],
    sync_frame_annotations: SyncFrameAnnotations = default_sync_frame_annotations,
    delete_frame_annotations_for_segment: DeleteFrameAnnotations = (
        default_delete_frame_annotations
    ),
) -> dict[str, Any]:
    mutation_request = validate_bulk_segment_mutation_payload(payload)

    created_segments: list[dict[str, Any]] = []
    created_segment_objects: list[LabelVideoSegment] = []
    updated_segments: list[LabelVideoSegment] = []
    deleted_segment_ids: list[int] = []
    attached_segment_ids: list[int] = []

    try:
        with transaction.atomic():
            _create_segments(
                video=video,
                creates=mutation_request.creates,
                defer_annotation_sync=mutation_request.defer_annotation_sync,
                created_segments=created_segments,
                created_segment_objects=created_segment_objects,
                sync_frame_annotations=sync_frame_annotations,
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
            )
            _delete_segments(
                video=video,
                deletes=mutation_request.deletes,
                deleted_segment_ids=deleted_segment_ids,
                delete_frame_annotations_for_segment=(
                    delete_frame_annotations_for_segment
                ),
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
    payload: Mapping[str, Any],
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


def _invalid_bulk_payload(detail: Any) -> BulkSegmentMutationServiceError:
    return BulkSegmentMutationServiceError(
        {"error": "Invalid bulk segment payload", "details": detail},
        status.HTTP_400_BAD_REQUEST,
    )


def _create_segments(
    *,
    video: VideoFile,
    creates: list[Any],
    defer_annotation_sync: bool,
    created_segments: list[dict[str, Any]],
    created_segment_objects: list[LabelVideoSegment],
    sync_frame_annotations: SyncFrameAnnotations,
) -> None:
    for index, item in enumerate(creates):
        if not isinstance(item, dict):
            raise _bulk_item_validation_error(
                "creates",
                index,
                "Each create entry must be an object.",
            )

        client_id = item.get("client_id")
        segment_payload = dict(item)
        segment_payload.pop("client_id", None)
        segment_payload["video_id"] = video.pk

        serializer = LabelVideoSegmentSerializer(data=segment_payload)
        if not serializer.is_valid():
            raise _bulk_item_validation_error("creates", index, serializer.errors)

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
    updates: list[Any],
    defer_annotation_sync: bool,
    updated_segments: list[LabelVideoSegment],
    sync_frame_annotations: SyncFrameAnnotations,
    delete_frame_annotations_for_segment: DeleteFrameAnnotations,
) -> None:
    for index, item in enumerate(updates):
        if not isinstance(item, dict):
            raise _bulk_item_validation_error(
                "updates",
                index,
                "Each update entry must be an object.",
            )
        if "id" not in item:
            raise _bulk_item_validation_error(
                "updates",
                index,
                {"id": "This field is required."},
            )

        try:
            segment_id = int(item["id"])
        except (TypeError, ValueError):
            raise _bulk_item_validation_error(
                "updates",
                index,
                {"id": "Must be an integer."},
            )

        segment = get_object_or_404(
            LabelVideoSegment.objects.select_related("video_file", "label", "source"),
            id=segment_id,
            video_file=video,
        )
        old_snapshot = _segment_snapshot(segment)

        segment_payload = dict(item)
        segment_payload.pop("id", None)
        serializer = LabelVideoSegmentSerializer(
            segment,
            data=segment_payload,
            partial=True,
        )
        if not serializer.is_valid():
            raise _bulk_item_validation_error("updates", index, serializer.errors)

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
    deletes: list[Any],
    deleted_segment_ids: list[int],
    delete_frame_annotations_for_segment: DeleteFrameAnnotations,
) -> None:
    for index, item in enumerate(deletes):
        segment_id = _bulk_delete_segment_id(item, index)
        segment = get_object_or_404(
            LabelVideoSegment.objects.select_related("video_file", "label", "source"),
            id=segment_id,
            video_file=video,
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
    created_segments: list[dict[str, Any]],
    updated_segments: list[LabelVideoSegment],
    deleted_segment_ids: list[int],
    attached_segment_ids: list[int],
) -> dict[str, Any]:
    ai_dataset = mutation_request.ai_dataset
    return {
        "created": created_segments,
        "updated": LabelVideoSegmentTimelineSerializer(
            updated_segments,
            many=True,
        ).data,
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


def _resolve_optional_ai_dataset(payload: Mapping[str, Any]) -> AIDataSet | None:
    raw_value = payload.get("ai_dataset_id")
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


def _require_bulk_list(data: Mapping[str, Any], field_name: str) -> list[Any]:
    value = data.get(field_name, [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise DRFValidationError({field_name: "Must be a list."})
    return value


def _bulk_delete_segment_id(item: Any, index: int) -> int:
    raw_id = item.get("id") if isinstance(item, dict) else item
    if raw_id is None:
        raise _bulk_item_validation_error(
            "deletes",
            index,
            "Each delete entry must be a segment id.",
        )
    try:
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
    detail: Any,
) -> DRFValidationError:
    indexed_errors: Mapping[str, Any] = {str(index): detail}
    bulk_errors: Mapping[str, Mapping[str, Any]] = {field_name: indexed_errors}
    return DRFValidationError(bulk_errors)


def _query_param_as_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _segment_snapshot(segment: LabelVideoSegment) -> dict[str, Any]:
    model_meta = segment.get_model_meta()
    return {
        "video": segment.video_file,
        "start_frame_number": segment.start_frame_number,
        "end_frame_number": segment.end_frame_number,
        "label": segment.label,
        "information_source_id": segment.source_id,
        "model_meta_id": model_meta.pk if model_meta else None,
    }


def _timeline_segment_data(segment: LabelVideoSegment) -> dict[str, Any]:
    return LabelVideoSegmentTimelineSerializer(segment).data

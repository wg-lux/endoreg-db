"""
Modern Media Framework - Video Segments Views
Migrated from legacy label_video_segment views (October 14, 2025)

Provides RESTful endpoints for video segment management:
- Collection: GET/POST /api/media/videos/segments/
- Detail: GET/PATCH/DELETE /api/media/videos/<pk>/segments/<segment_id>/
- Video-specific: GET/POST /api/media/videos/<pk>/segments/
"""

import logging
import os
import uuid
from collections.abc import Mapping
from typing import Any, cast

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q, QuerySet
from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from pydantic import ValidationError as PydanticValidationError
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.request import Request
from rest_framework.response import Response
from lx_dtypes.models.contracts.video_segments import (
    validate_segment_annotation_ensure_payload,
    validate_segment_blacken_outside_payload,
    validate_segment_bulk_validation_payload,
    validate_segment_crud_payload,
    validate_segment_list_query,
    validate_segment_prediction_import_payload,
    validate_segment_validation_payload,
    validate_segment_validation_status_payload,
)
from endoreg_db.models.aidataset.aidataset import AIDataSet
from endoreg_db.models.label.annotation.image_classification import (
    ImageClassificationAnnotation,
)
from endoreg_db.models.label.label import Label
from endoreg_db.models.label.label import LabelManager
from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.other.information_source import InformationSource
from endoreg_db.models.state.video_segment_validation import (
    mark_segment_annotations_complete_without_cleanup,
    mark_segment_annotations_pending_cleanup,
    mark_segment_annotations_stale,
    resolve_segment_annotation_status,
)
from endoreg_db.services.segment_annotations import (
    ensure_prediction_segment_annotations,
    ensure_segment_annotations,
)
from endoreg_db.services.segment_frame_annotations import (
    delete_frame_annotations_for_segment as service_delete_frame_annotations_for_segment,
    sync_frame_annotations_for_segment as service_sync_frame_annotations_for_segment,
)

from endoreg_db.services.video_segments_bulk_mutation import (
    BulkSegmentMutationServiceError,
    bulk_mutate_video_segments,
)
from endoreg_db.services.media_operation_gate import (
    create_segment_update_lease_on_commit,
)
from endoreg_db.services.jobs.video_post_validation_jobs import (
    JobDispatchResult,
    dispatch_video_post_validation_rebuild,
)
from endoreg_db.services.jobs.video_fps_normalization_jobs import (
    dispatch_video_fps_normalization,
    normalization_status,
)
from endoreg_db.services.video_files import (
    get_or_create_video_state,
    video_seconds_to_frame_number,
)
from endoreg_db.models.state.label_video_segment import LabelVideoSegmentState
from endoreg_db.serializers.label_video_segment import (
    LabelVideoSegmentTimelineSerializer,
    LabelVideoSegmentSerializer,
)
from endoreg_db.utils.permissions import EnvironmentAwarePermission

from endoreg_db.utils.operation_log import (
    record_operation,
    ACTION_SEGMENT_ANNOTATED,
    STATUS_VALIDATED,
    STATUS_UNVALIDATED,
)

logger = logging.getLogger(__name__)

SegmentSnapshot = dict[str, Any]
PREDICTION_CORRECTION_SOURCE_NAME = "prediction_correction"


def _request_payload(request: Request) -> Mapping[str, Any]:
    payload = cast(object, request.data)
    if isinstance(payload, Mapping):
        return cast(Mapping[str, Any], payload)
    return {}


def _request_query(request: Request) -> Mapping[str, Any]:
    query_params = cast(object, request.query_params)
    query_dict = getattr(query_params, "dict", None)
    if callable(query_dict):
        return cast(dict[str, Any], query_dict())
    if isinstance(query_params, Mapping):
        return cast(Mapping[str, Any], query_params)
    return {}


def _pydantic_error_response(
    exc: PydanticValidationError,
    *,
    message: str = "Invalid payload",
) -> Response:
    return Response(
        {"error": message, "details": exc.errors(include_context=False)},
        status=status.HTTP_400_BAD_REQUEST,
    )


def _serializer_data(serializer: Any) -> Any:
    return serializer.data


def _serializer_errors(serializer: Any) -> Any:
    return serializer.errors


def _sync_frame_annotations(
    *,
    segment: LabelVideoSegment,
    old_snapshot: SegmentSnapshot | None = None,
) -> None:
    service_sync_frame_annotations_for_segment(
        segment=segment,
        old_snapshot=old_snapshot,
    )


def _delete_frame_annotations_for_segment(
    *,
    video: VideoFile,
    start_frame_number: int,
    end_frame_number: int,
    label: Label | None,
    information_source_id: int | None,
    model_meta_id: int | None,
) -> int:
    return service_delete_frame_annotations_for_segment(
        video=video,
        start_frame_number=start_frame_number,
        end_frame_number=end_frame_number,
        label=label,
        information_source_id=information_source_id,
        model_meta_id=model_meta_id,
    )


def _segment_pk(segment: LabelVideoSegment) -> int:
    return int(segment.pk)


def _segment_label(segment: LabelVideoSegment) -> Label | None:
    return cast(Label | None, cast(Any, segment).label)


def _segment_source(segment: LabelVideoSegment) -> InformationSource | None:
    return cast(InformationSource | None, cast(Any, segment).source)


def _segment_video_file(segment: LabelVideoSegment) -> VideoFile:
    return cast(VideoFile, cast(Any, segment).video_file)


def _segment_start_frame(segment: LabelVideoSegment) -> int:
    return int(cast(Any, segment).start_frame_number)


def _segment_end_frame(segment: LabelVideoSegment) -> int:
    return int(cast(Any, segment).end_frame_number)


def _label_name(label: Label | None) -> str | None:
    return cast(str, cast(Any, label).name) if label is not None else None


def _segment_label_name(segment: LabelVideoSegment) -> str | None:
    return _label_name(_segment_label(segment))


def _segment_source_id(segment: LabelVideoSegment) -> int | None:
    return cast(int | None, getattr(segment, "source_id", None))


def _validate_segment_frame_range(
    start_frame_number: int,
    end_frame_number: int,
    *,
    video_file: VideoFile,
) -> None:
    cast(Any, LabelVideoSegment).validate_frame_range(
        start_frame_number,
        end_frame_number,
        video_file=video_file,
    )


def _save_segment(
    segment: LabelVideoSegment,
    *,
    update_fields: list[str] | None = None,
) -> None:
    if update_fields is None:
        cast(Any, segment).save()
    else:
        cast(Any, segment).save(update_fields=update_fields)


def _delete_segment(segment: LabelVideoSegment) -> None:
    cast(Any, segment).delete()


def _segment_snapshot(segment: LabelVideoSegment) -> SegmentSnapshot:
    model_meta = segment.get_model_meta()
    return {
        "video": _segment_video_file(segment),
        "start_frame_number": _segment_start_frame(segment),
        "end_frame_number": _segment_end_frame(segment),
        "label": _segment_label(segment),
        "information_source_id": _segment_source_id(segment),
        "model_meta_id": model_meta.pk if model_meta else None,
    }


def _prediction_segment_query() -> Q:
    return Q(prediction_meta__isnull=False) | Q(source__name="prediction")


def _prediction_correction_segment_query() -> Q:
    return Q(source__name=PREDICTION_CORRECTION_SOURCE_NAME)


def _filter_segments_by_origin(
    queryset: QuerySet[LabelVideoSegment],
    source_kind: str | None,
) -> QuerySet[LabelVideoSegment]:
    normalized = str(source_kind or "all").strip().lower()
    if normalized == "prediction":
        return queryset.filter(_prediction_segment_query()).distinct()
    if normalized == PREDICTION_CORRECTION_SOURCE_NAME:
        return queryset.filter(_prediction_correction_segment_query()).distinct()
    if normalized == "manual":
        return (
            queryset.exclude(_prediction_segment_query())
            .exclude(_prediction_correction_segment_query())
            .distinct()
        )
    return queryset


def _resolve_optional_ai_dataset(
    payload: Mapping[str, Any],
) -> tuple[AIDataSet | None, Response | None]:
    raw_value = payload.get("ai_dataset_id")
    if raw_value in (None, ""):
        return None, None
    try:
        dataset_id = int(str(raw_value).strip())
    except (TypeError, ValueError):
        return (
            None,
            Response(
                {"error": "ai_dataset_id must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            ),
        )

    dataset = AIDataSet.objects.filter(pk=dataset_id).first()
    if dataset is None:
        return (
            None,
            Response(
                {
                    "error": "AIDataSet not found.",
                    "details": {"ai_dataset_id": dataset_id},
                },
                status=status.HTTP_404_NOT_FOUND,
            ),
        )
    return dataset, None


def _normalized_annotator(annotator: str | None) -> str | None:
    if annotator is None:
        return None
    normalized = str(annotator).strip()
    return normalized or None


def _segment_annotation_integrity_errors(
    segments: list[LabelVideoSegment],
    *,
    annotator: str | None,
) -> list[dict[str, object]]:
    normalized_annotator = _normalized_annotator(annotator)
    errors: list[dict[str, object]] = []
    segment_data: list[
        tuple[
            int,
            Label,
            InformationSource,
            int | None,
            list[tuple[int, int]],
        ]
    ] = []
    all_frame_ids: list[int] = []

    for segment in segments:
        segment_pk = _segment_pk(segment)
        label = _segment_label(segment)
        if label is None:
            errors.append({"segment_id": segment_pk, "reason": "missing_label"})
            continue
        information_source = _segment_source(segment)
        if information_source is None:
            errors.append(
                {"segment_id": segment_pk, "reason": "missing_information_source"}
            )
            continue
        frames = list(segment.get_frames().only("id", "frame_number"))
        if not frames:
            errors.append({"segment_id": segment_pk, "reason": "missing_frames"})
            continue
        try:
            model_meta = segment.get_model_meta()
        except Exception:
            model_meta = None

        frame_pairs = [
            (int(cast(Any, frame).pk), int(cast(Any, frame).frame_number))
            for frame in frames
        ]
        segment_data.append(
            (
                segment_pk,
                label,
                information_source,
                model_meta.pk if model_meta else None,
                frame_pairs,
            )
        )
        all_frame_ids.extend(frame_id for frame_id, _frame_number in frame_pairs)

    if not segment_data:
        return errors

    annotations = ImageClassificationAnnotation.objects.filter(
        frame_id__in=all_frame_ids
    )
    if normalized_annotator is not None:
        annotations = annotations.filter(annotator=normalized_annotator)

    annotation_rows = annotations.values_list(
        "frame_id",
        "label_id",
        "information_source_id",
        "model_meta_id",
        "annotator",
    )

    annotated_keys = {
        (
            int(frame_id),
            int(label_id),
            int(information_source_id) if information_source_id is not None else None,
            int(model_meta_id) if model_meta_id is not None else None,
            _normalized_annotator(cast(str | None, row_annotator)),
        )
        for (
            frame_id,
            label_id,
            information_source_id,
            model_meta_id,
            row_annotator,
        ) in annotation_rows
        if label_id is not None
    }

    for (
        segment_pk,
        label,
        information_source,
        model_meta_id,
        frame_pairs,
    ) in segment_data:
        missing_frame_numbers: list[int] = [
            frame_number
            for frame_id, frame_number in frame_pairs
            if (
                frame_id,
                int(label.pk),
                int(information_source.pk),
                model_meta_id,
                normalized_annotator,
            )
            not in annotated_keys
        ]
        if missing_frame_numbers:
            errors.append(
                {
                    "segment_id": segment_pk,
                    "reason": "missing_frame_annotations",
                    "missing_frame_numbers": missing_frame_numbers[:10],
                    "missing_count": len(missing_frame_numbers),
                }
            )
    return errors


def _dispatch_segment_annotation_expansion(
    *,
    video_id: int,
    segment_ids: list[int],
    information_source_name: str,
    annotator: str | None,
    dispatch_post_validation_rebuild: bool = False,
    mark_complete_without_rebuild: bool | None = None,
) -> tuple[str, bool]:
    from endoreg_db.tasks import run_segment_annotation_expansion_task

    kwargs = {
        "video_id": int(video_id),
        "segment_ids": [int(segment_id) for segment_id in segment_ids],
        "information_source_name": information_source_name,
        "annotator": annotator,
    }
    if bool(getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)) or (
        "PYTEST_CURRENT_TEST" in os.environ
    ):
        result = run_segment_annotation_expansion_task.apply(kwargs=kwargs)
        return str(result.id), True
    kwargs["dispatch_post_validation_rebuild"] = bool(dispatch_post_validation_rebuild)
    kwargs["mark_complete_without_rebuild"] = bool(
        not dispatch_post_validation_rebuild
        if mark_complete_without_rebuild is None
        else mark_complete_without_rebuild
    )
    result = run_segment_annotation_expansion_task.apply_async(kwargs=kwargs)
    return str(result.id), False


def _has_outside_cleanup_targets(video: VideoFile) -> bool:
    from importlib import import_module

    segment_services = import_module("endoreg_db.services.video_files._segments")
    return bool(
        cast(Any, segment_services)
        ._get_outside_frames(video, only_validated=False)
        .exists()
    )


def _bulk_validation_response_status(post_processing_status: str | None) -> int:
    if post_processing_status in {"queued", "already_queued"}:
        return status.HTTP_202_ACCEPTED
    if post_processing_status == "busy":
        return status.HTTP_409_CONFLICT
    if post_processing_status == "failed":
        return status.HTTP_503_SERVICE_UNAVAILABLE
    return status.HTTP_200_OK


def _validation_status_from_job(post_processing_job: JobDispatchResult | None) -> str:
    if post_processing_job is None:
        return "completed"
    if post_processing_job.validation_status:
        return post_processing_job.validation_status
    if post_processing_job.status in {"queued", "already_queued"}:
        return "scheduled"
    if post_processing_job.status == "busy":
        return "running"
    if post_processing_job.status == "failed":
        return "failed"
    return "completed"


def _segment_validation_state_payload(video: VideoFile) -> dict[str, object]:
    state = get_or_create_video_state(video)
    return {
        "segment_annotation_status": resolve_segment_annotation_status(video),
        "segment_annotations_validated": bool(
            getattr(state, "segment_annotations_validated", False)
        ),
        "outside_segments_removed": bool(
            getattr(state, "outside_segments_removed", False)
        ),
    }


@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission])
def video_segments_blacken_outside(request: Request, pk: int) -> Response:
    """
    POST /api/media/videos/<pk>/segments/blacken-outside/

    Explicitly rebuild the processed video with frames from "outside" segments
    blackened. Body:
        {
            "only_validated": false
        }
    """
    try:
        payload = validate_segment_blacken_outside_payload(_request_payload(request))
    except PydanticValidationError as exc:
        return _pydantic_error_response(exc)

    video = get_object_or_404(VideoFile, pk=pk)
    only_validated = payload.only_validated
    outside_segments = LabelVideoSegment.objects.filter(
        video_file=video,
        label__name__iexact="outside",
    )
    if only_validated:
        outside_segments = outside_segments.filter(state__is_validated=True)
    outside_segment_count = outside_segments.count()
    segment_rows = LabelVideoSegment.objects.filter(video_file=video)

    if (
        segment_rows.exists()
        and segment_rows.exclude(state__is_validated=True).exists()
    ):
        return Response(
            {
                "message": "All video segments must be validated before blackening.",
                "status": "validation_required",
                "video_id": video.pk,
                "validation_status": "validation_required",
            },
            status=status.HTTP_409_CONFLICT,
        )

    if outside_segment_count == 0:
        return Response(
            {
                "message": "No matching outside segments found",
                "status": "noop",
                "video_id": video.pk,
                "outside_segment_count": 0,
                "only_validated": only_validated,
                "validation_status": "completed",
            },
            status=status.HTTP_200_OK,
        )

    if not only_validated:
        return Response(
            {
                "message": "Post-validation blackening requires only_validated=true.",
                "status": "validation_required",
                "video_id": video.pk,
                "validation_status": "validation_required",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        post_processing_job = dispatch_video_post_validation_rebuild(
            video_id=video.pk,
            only_validated=only_validated,
        )
    except Exception as exc:
        logger.exception(
            "Outside-frame blackening dispatch failed for video %s.", video.pk
        )
        return Response(
            {
                "message": "Outside-frame blackening failed",
                "status": "failed",
                "operation": "blacken_outside",
                "video_id": video.pk,
                "outside_segment_count": outside_segment_count,
                "only_validated": only_validated,
                "validation_status": "failed",
                "error": str(exc),
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    message_by_status = {
        "already_queued": "Outside-frame blackening already queued",
        "busy": "Video reprocessing is already running",
        "completed": "Outside-frame blackening completed",
        "failed": "Outside-frame blackening failed",
    }
    response_status: int = status.HTTP_202_ACCEPTED
    if post_processing_job.status == "busy":
        response_status = status.HTTP_409_CONFLICT
    elif post_processing_job.status == "failed":
        response_status = status.HTTP_500_INTERNAL_SERVER_ERROR

    return Response(
        {
            "message": message_by_status.get(
                post_processing_job.status,
                "Outside-frame blackening queued",
            ),
            "status": post_processing_job.status,
            "operation": "blacken_outside",
            "video_id": video.pk,
            "outside_segment_count": outside_segment_count,
            "only_validated": only_validated,
            "validation_status": _validation_status_from_job(post_processing_job),
            "post_processing_job": post_processing_job.to_dict(),
        },
        status=response_status,
    )


@api_view(["GET", "POST"])
@permission_classes([EnvironmentAwarePermission])
def video_segments_normalize_fps(request: Request, pk: int) -> Response:
    """Start or inspect idempotent pre-annotation FPS normalization."""
    video = get_object_or_404(VideoFile, pk=pk)
    try:
        result = (
            dispatch_video_fps_normalization(video)
            if request.method == "POST"
            else normalization_status(video)
        )
    except (TypeError, ValueError) as exc:
        return Response(
            {
                "error": "Could not determine a valid source FPS.",
                "detail": str(exc),
                "status": "failed",
                "video_id": int(video.pk),
            },
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    response_status = (
        status.HTTP_202_ACCEPTED
        if result.status in {"queued", "already_queued", "running"}
        else status.HTTP_200_OK
    )
    return Response(result.to_dict(), status=response_status)


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
def video_segments_stats(request: Request) -> Response:
    """
    Statistics endpoint for video segments.

    GET /api/media/videos/segments/stats/
    Returns aggregated statistics about video segments.
    """
    try:
        # Get all segments queryset
        segments = LabelVideoSegment.objects.all()

        # Calculate statistics
        total_segments = segments.count()

        # Segments by label
        label_counts = segments.values("label__name").annotate(count=Count("id"))

        # Videos with segments
        videos_with_segments = segments.values("video_file").distinct().count()

        stats = {
            "total_segments": total_segments,
            "videos_with_segments": videos_with_segments,
            "by_label": {
                item["label__name"]: item["count"]
                for item in label_counts
                if item["label__name"]
            },
        }

        return Response(stats, status=status.HTTP_200_OK)

    except Exception as e:
        logger.error(f"Error fetching video segment stats: {e}")
        return Response(
            {"error": "Failed to fetch segment statistics"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["GET", "POST"])
@permission_classes([EnvironmentAwarePermission])
def video_segments_collection(request: Request) -> Response:
    """
    Collection endpoint for all video segments across all videos.

    GET /api/media/videos/segments/
    - Lists all segments, optionally filtered by video_id and/or label_id
    - Query params: video_id, label_id, include_annotation_payload, source_kind
      (set include_annotation_payload=1 to include expensive frame-level data)

    POST /api/media/videos/segments/
    - Creates a new video segment
    - Requires: video_id, label_id, start_frame_number, end_frame_number

    Modern replacement for: /api/video-segments/
    """
    if request.method == "POST":
        logger.info(f"Creating new video segment with data: {request.data}")

        try:
            crud_payload = validate_segment_crud_payload(_request_payload(request))
        except PydanticValidationError as exc:
            return _pydantic_error_response(exc)

        ai_dataset, ai_dataset_error = _resolve_optional_ai_dataset(
            {"ai_dataset_id": crud_payload.ai_dataset_id}
        )
        if ai_dataset_error is not None:
            return ai_dataset_error
        data = crud_payload.serializer_payload()

        with transaction.atomic():
            serializer = LabelVideoSegmentSerializer(data=data)
            if serializer.is_valid():
                try:
                    segment = serializer.save()
                    _sync_frame_annotations(segment=segment)
                    if ai_dataset is not None:
                        ai_dataset.add_video_annotations([segment])
                    logger.info(f"Successfully created video segment {segment.pk}")
                    return Response(
                        _serializer_data(LabelVideoSegmentSerializer(segment)),
                        status=status.HTTP_201_CREATED,
                    )
                except Exception as e:
                    logger.error(f"Error creating video segment: {str(e)}")
                    return Response(
                        {"error": f"Failed to create segment: {str(e)}"},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    )
            else:
                details = _serializer_errors(serializer)
                logger.warning(f"Invalid data for video segment creation: {details}")
                return Response(
                    {"error": "Invalid data", "details": details},
                    status=status.HTTP_400_BAD_REQUEST,
                )

    elif request.method == "GET":
        try:
            query = validate_segment_list_query(_request_query(request))
        except PydanticValidationError as exc:
            return _pydantic_error_response(exc, message="Invalid query parameters")

        queryset = LabelVideoSegment.objects.select_related("video_file", "label").all()

        if query.video_id is not None:
            try:
                video = VideoFile.objects.get(id=query.video_id)
                queryset = queryset.filter(video_file=video)
            except VideoFile.DoesNotExist:
                return Response(
                    {"error": f"Video with id {query.video_id} not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )

        if query.label_id is not None:
            try:
                label = Label.objects.get(id=query.label_id)
                queryset = queryset.filter(label=label)
            except Label.DoesNotExist:
                return Response(
                    {"error": f"Label with id {query.label_id} not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )

        queryset = _filter_segments_by_origin(queryset, query.source_kind)

        # Order by video and start time for consistent results
        segments = queryset.order_by("video_file__id", "start_frame_number")
        serializer = LabelVideoSegmentSerializer(
            segments,
            many=True,
            context={
                "request": request,
                "include_annotation_payload": query.include_annotation_payload,
            },
        )
        return Response(_serializer_data(serializer))
    return Response(
        {"error": f"Method {request.method} not allowed"},
        status=status.HTTP_405_METHOD_NOT_ALLOWED,
    )


@api_view(["GET", "POST"])
@permission_classes([EnvironmentAwarePermission])
def video_segments_by_video(request: Request, pk: int) -> Response:
    """
    Video-specific segments endpoint.

    GET /api/media/videos/<pk>/segments/
    - Lists all segments for a specific video
    - Query params: label (label name filter), include_annotation_payload, source_kind
      (set include_annotation_payload=1 to include expensive frame-level data)
    - Note: This was already implemented in segments.py as video_segments_by_pk

    POST /api/media/videos/<pk>/segments/
    - Creates a new segment for this video
    - Automatically sets video_id to pk
    - Requires: label_id, start_frame_number, end_frame_number

    Modern replacement for: /api/video-segments/?video_id=<pk>
    """
    # Verify video exists
    video = get_object_or_404(VideoFile, id=pk)

    if request.method == "GET":
        try:
            query = validate_segment_list_query(_request_query(request))
        except PydanticValidationError as exc:
            return _pydantic_error_response(exc, message="Invalid query parameters")

        queryset = LabelVideoSegment.objects.filter(video_file=video).select_related(
            "video_file", "label"
        )

        if query.label:
            label = cast(LabelManager, Label.objects).resolve_by_name(query.label)
            if label is None:
                return Response(
                    {"error": f'Label "{query.label}" not found'},
                    status=status.HTTP_404_NOT_FOUND,
                )
            queryset = queryset.filter(label=label)

        queryset = _filter_segments_by_origin(queryset, query.source_kind)

        segments = queryset.order_by("start_frame_number")
        serializer = LabelVideoSegmentSerializer(
            segments,
            many=True,
            context={
                "request": request,
                "include_annotation_payload": query.include_annotation_payload,
            },
        )
        return Response(_serializer_data(serializer))

    elif request.method == "POST":
        logger.info(f"Creating new segment for video {pk} with data: {request.data}")

        try:
            crud_payload = validate_segment_crud_payload(_request_payload(request))
        except PydanticValidationError as exc:
            return _pydantic_error_response(exc)

        ai_dataset, ai_dataset_error = _resolve_optional_ai_dataset(
            {"ai_dataset_id": crud_payload.ai_dataset_id}
        )
        if ai_dataset_error is not None:
            return ai_dataset_error
        data = crud_payload.serializer_payload(video_id=pk)

        with transaction.atomic():
            serializer = LabelVideoSegmentSerializer(data=data)
            if serializer.is_valid():
                try:
                    segment = serializer.save()
                    _sync_frame_annotations(segment=segment)
                    if ai_dataset is not None:
                        ai_dataset.add_video_annotations([segment])
                    logger.info(
                        f"Successfully created segment {segment.pk} for video {pk}"
                    )
                    return Response(
                        _serializer_data(LabelVideoSegmentSerializer(segment)),
                        status=status.HTTP_201_CREATED,
                    )
                except Exception as e:
                    logger.error(f"Error creating segment for video {pk}: {str(e)}")
                    return Response(
                        {"error": f"Failed to create segment: {str(e)}"},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    )
            else:
                details = _serializer_errors(serializer)
                logger.warning(f"Invalid data for segment creation: {details}")
                return Response(
                    {"error": "Invalid data", "details": details},
                    status=status.HTTP_400_BAD_REQUEST,
                )
    return Response(
        {"error": f"Method {request.method} not allowed"},
        status=status.HTTP_405_METHOD_NOT_ALLOWED,
    )


@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission])
def video_segments_bulk_mutation(request: Request, pk: int) -> Response:
    """
    Bulk mutate manual timeline segments for a video.

    POST /api/media/videos/<pk>/segments/bulk/

    Body:
    {
      "defer_annotation_sync": true,
      "creates": [
        {
          "client_id": -1,
          "label_id": 1,
          "start_time": 1.2,
          "end_time": 3.4,
          "export_segment": false
        }
      ],
      "updates": [
        {
          "id": 123,
          "start_time": 1.2,
          "end_time": 3.4,
          "export_segment": true
        }
      ],
      "deletes": [456]
    }
    """
    video = get_object_or_404(VideoFile, id=pk)
    try:
        response_data = bulk_mutate_video_segments(
            video=video,
            payload=_request_payload(request),
            sync_frame_annotations=_sync_frame_annotations,
            delete_frame_annotations_for_segment=(
                _delete_frame_annotations_for_segment
            ),
        )
    except BulkSegmentMutationServiceError as exc:
        return Response(
            exc.response_data,
            status=exc.status_code,
        )

    return Response(
        response_data,
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission])
def import_prediction_segments_to_manual(request: Request, pk: int) -> Response:
    """
    Replace or extend the prediction-correction segment layer for a video using
    a caller-supplied segment list adjusted from pipe-1 predictions in the UI.

    Prediction and ordinary manual segment layers are always preserved.

    POST /api/media/videos/<pk>/segments/import-predictions/

    Body:
    {
      "segments": [
        {"label_name": "outside", "start_time": 1.2, "end_time": 3.4},
        ...
      ],
      "replace_existing": true
    }
    """
    video = get_object_or_404(VideoFile, id=pk)
    try:
        import_payload = validate_segment_prediction_import_payload(
            _request_payload(request)
        )
    except PydanticValidationError as exc:
        return _pydantic_error_response(
            exc,
            message="Invalid segment import payload",
        )

    correction_source, _ = InformationSource.objects.get_or_create(
        name=PREDICTION_CORRECTION_SOURCE_NAME,
        defaults={
            "description": (
                "Human corrections derived from immutable video segment predictions"
            )
        },
    )

    validated_serializers: list[LabelVideoSegmentSerializer] = []
    for idx, item in enumerate(import_payload.segments):
        payload = item.serializer_payload(video_id=pk)
        serializer = LabelVideoSegmentSerializer(data=payload)
        if not serializer.is_valid():
            details = _serializer_errors(serializer)
            return Response(
                {
                    "error": "Invalid segment import payload",
                    "details": details,
                    "segment_index": idx,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        validated_serializers.append(serializer)

    created_segments: list[LabelVideoSegment] = []
    with transaction.atomic():
        if import_payload.replace_existing:
            correction_segments = LabelVideoSegment.objects.filter(
                video_file=video,
            ).filter(_prediction_correction_segment_query())
            for segment in correction_segments.iterator():
                segment_label = _segment_label(segment)
                if segment_label is not None:
                    delete_model_meta = segment.get_model_meta()
                    _delete_frame_annotations_for_segment(
                        video=_segment_video_file(segment),
                        start_frame_number=_segment_start_frame(segment),
                        end_frame_number=_segment_end_frame(segment),
                        label=segment_label,
                        information_source_id=_segment_source_id(segment),
                        model_meta_id=(
                            delete_model_meta.pk if delete_model_meta else None
                        ),
                    )
                _delete_segment(segment)

        for serializer in validated_serializers:
            segment = serializer.save()
            if getattr(segment, "source_id", None) != getattr(correction_source, "pk"):
                segment.source = correction_source
                _save_segment(segment, update_fields=["source"])
            _sync_frame_annotations(segment=segment)
            created_segments.append(segment)

    response_serializer = LabelVideoSegmentTimelineSerializer(
        created_segments,
        many=True,
    )
    return Response(
        {
            "message": "Prediction corrections imported to a separate annotation track.",
            "created_count": len(created_segments),
            "replaced_existing": import_payload.replace_existing,
            "source_name": PREDICTION_CORRECTION_SOURCE_NAME,
            "segments": _serializer_data(response_serializer),
        },
        status=status.HTTP_200_OK,
    )


@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([EnvironmentAwarePermission])
def video_segment_detail(request: Request, pk: int, segment_id: int) -> Response:
    """
    Detail endpoint for a specific video segment.

    GET /api/media/videos/<pk>/segments/<segment_id>/
    - Returns segment details

    PATCH /api/media/videos/<pk>/segments/<segment_id>/
    - Updates segment (partial update)

    DELETE /api/media/videos/<pk>/segments/<segment_id>/
    - Deletes segment

    Modern replacement for: /api/video-segments/<segment_id>/
    """
    # Verify video exists
    video = get_object_or_404(VideoFile, id=pk)

    # Get segment and verify it belongs to this video
    segment = get_object_or_404(LabelVideoSegment, id=segment_id, video_file=video)

    if request.method == "GET":
        serializer = LabelVideoSegmentSerializer(segment)
        return Response(_serializer_data(serializer))

    elif request.method == "PATCH":
        logger.info(
            f"Updating segment {segment_id} for video {pk} with data: {request.data}"
        )

        try:
            crud_payload = validate_segment_crud_payload(_request_payload(request))
        except PydanticValidationError as exc:
            return _pydantic_error_response(exc)

        ai_dataset, ai_dataset_error = _resolve_optional_ai_dataset(
            {"ai_dataset_id": crud_payload.ai_dataset_id}
        )
        if ai_dataset_error is not None:
            return ai_dataset_error
        data = crud_payload.serializer_payload()

        with transaction.atomic():
            old_snapshot = _segment_snapshot(segment)
            serializer = LabelVideoSegmentSerializer(segment, data=data, partial=True)
            if serializer.is_valid():
                try:
                    segment = serializer.save()
                    _sync_frame_annotations(
                        segment=segment,
                        old_snapshot=old_snapshot,
                    )
                    if ai_dataset is not None:
                        ai_dataset.add_video_annotations([segment])
                    logger.info(f"Successfully updated segment {segment_id}")
                    return Response(
                        _serializer_data(LabelVideoSegmentSerializer(segment))
                    )
                except Exception as e:
                    logger.error(f"Error updating segment {segment_id}: {str(e)}")
                    return Response(
                        {"error": f"Failed to update segment: {str(e)}"},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    )
            else:
                details = _serializer_errors(serializer)
                logger.warning(f"Invalid data for segment update: {details}")
                return Response(
                    {"error": "Invalid data", "details": details},
                    status=status.HTTP_400_BAD_REQUEST,
                )

    elif request.method == "DELETE":
        logger.info(f"Deleting segment {segment_id} from video {pk}")
        try:
            with transaction.atomic():
                segment_label = _segment_label(segment)
                if segment_label is not None:
                    delete_model_meta = segment.get_model_meta()
                    _delete_frame_annotations_for_segment(
                        video=_segment_video_file(segment),
                        start_frame_number=_segment_start_frame(segment),
                        end_frame_number=_segment_end_frame(segment),
                        label=segment_label,
                        information_source_id=_segment_source_id(segment),
                        model_meta_id=(
                            delete_model_meta.pk if delete_model_meta else None
                        ),
                    )
                _delete_segment(segment)
                logger.info(f"Successfully deleted segment {segment_id}")
                return Response(
                    {"message": f"Segment {segment_id} deleted successfully"},
                    status=status.HTTP_204_NO_CONTENT,
                )
        except Exception as e:
            logger.error(f"Error deleting segment {segment_id}: {str(e)}")
            return Response(
                {"error": f"Failed to delete segment: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
    return Response(
        {"error": f"Method {request.method} not allowed"},
        status=status.HTTP_405_METHOD_NOT_ALLOWED,
    )


@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission])
def video_segment_validate(request: Request, pk: int, segment_id: int) -> Response:
    """
    Validate a single video segment.

    POST /api/media/videos/<pk>/segments/<segment_id>/validate/

    Validates a single LabelVideoSegment and marks it as verified.
    Used to confirm user-reviewed segment annotations.

    Request Body (optional):
    {
      "is_validated": true,  // optional, default true
      "notes": "..."         // optional, validation notes
    }

    Response:
    {
      "message": "Segment validated successfully",
      "segment_id": 123,
      "is_validated": true,
      "label": "polyp",
      "video_id": 456,
      "start_frame": 100,
      "end_frame": 200
    }
    """
    # Verify video exists
    video = get_object_or_404(VideoFile, pk=pk)

    segment = get_object_or_404(
        LabelVideoSegment.objects.select_related("state", "video_file", "label"),
        pk=segment_id,
        video_file=video,
    )

    # for operation log table
    status_before = (
        STATUS_VALIDATED
        if (segment.state and segment.state.is_validated)
        else STATUS_UNVALIDATED
    )

    try:
        try:
            payload = validate_segment_validation_payload(_request_payload(request))
        except PydanticValidationError as exc:
            return _pydantic_error_response(exc)

        is_validated = payload.is_validated
        information_source_name = payload.information_source_name
        annotation_annotator = payload.annotator

        # Optional: update times (seconds) before validation
        annotation_input = payload.to_annotation_input(video_id=int(video.pk))
        with transaction.atomic():
            if annotation_input is not None:
                segment_video = _segment_video_file(segment)
                new_start = video_seconds_to_frame_number(
                    segment_video, annotation_input.start_time
                )
                new_end = video_seconds_to_frame_number(
                    segment_video, annotation_input.end_time
                )
                _validate_segment_frame_range(
                    new_start,
                    new_end,
                    video_file=segment_video,
                )
                segment.start_frame_number = new_start
                segment.end_frame_number = new_end
                _save_segment(
                    segment,
                    update_fields=["start_frame_number", "end_frame_number"],
                )

            segment.mark_validated(
                is_validated=is_validated,
                information_source_name=information_source_name,
            )
            segment_id = segment.pk

            def _log_after_commit():
                # re-read from DB to get the REAL final state
                segment.refresh_from_db()
                state = segment.state

                status_after = (
                    STATUS_VALIDATED
                    if (state and state.is_validated)
                    else STATUS_UNVALIDATED
                )

                record_operation(
                    cast(HttpRequest, request),
                    action=ACTION_SEGMENT_ANNOTATED,
                    resource_type="video_segment",
                    resource_id=segment.pk,
                    status_before=status_before,
                    status_after=status_after,
                    meta={
                        "video_id": video.pk,
                        "label": _segment_label_name(segment),
                        "information_source": information_source_name,
                        "annotator": annotation_annotator,
                    },
                )

            transaction.on_commit(_log_after_commit)
            create_segment_update_lease_on_commit(video)

            """
            status_after = STATUS_VALIDATED if is_validated else STATUS_UNVALIDATED

            record_operation(
                request,
                action=ACTION_SEGMENT_ANNOTATED,
                resource_type="video_segment",
                resource_id=segment.pk,
                status_before=status_before,
                status_after=status_after,
                meta={
                    "video_id": video.pk,
                    "label": segment.label.name if segment.label else None,
                    "information_source": information_source_name,
                },
            )"""
        annotation_task_id, expansion_completed = (
            _dispatch_segment_annotation_expansion(
                video_id=int(video.pk),
                segment_ids=[int(segment.pk)],
                information_source_name=information_source_name,
                annotator=annotation_annotator,
                dispatch_post_validation_rebuild=False,
                mark_complete_without_rebuild=False,
            )
        )
        if not expansion_completed:
            response_status = status.HTTP_202_ACCEPTED
            validation_status = "annotation_expansion_queued"
            post_processing_job_payload = None
        else:
            response_status = status.HTTP_200_OK
            validation_status = "completed"
            post_processing_job_payload = None

        logger.info(f"Validated segment {segment_id} in video {pk}: {is_validated}")

        response_data: dict[str, object] = {
            "message": f"Segment {segment_id} validation status updated",
            "segment_id": segment_id,
            "is_validated": is_validated,
            "label": _segment_label_name(segment),
            "video_id": video.pk,
            "start_frame": _segment_start_frame(segment),
            "end_frame": _segment_end_frame(segment),
            "validation_status": validation_status,
            "annotation_expansion_task_id": annotation_task_id,
        }
        if post_processing_job_payload is not None:
            response_data["post_processing_job"] = post_processing_job_payload

        return Response(response_data, status=response_status)
    except Exception as e:
        logger.error(f"Error validating segment {segment_id} in video {pk}: {e}")
        return Response(
            {"error": f"Validation failed: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


# TODO Pass user based information source to backend. This is the endpoint currently used by the VideoExamination endpoint
@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission])
def video_segments_validate_bulk(request: Request, pk: int) -> Response:
    """
    Validate multiple video segments at once.

    POST /api/media/videos/<pk>/segments/validate-bulk/

    Body:
    {
      "segment_ids": [1, 2, 3, ...],
      "segments": [
        {"id": 1, "start_time": 12.3, "end_time": 15.7},
        ...
      ],
      "is_validated": true,
      "notes": "...",
      "information_source_name": "manual_annotation"
    }

    THIS IS WHERE SEGMENTS ARE STORED IN THE DATABASE
    """
    video = get_object_or_404(VideoFile, pk=pk)

    try:
        payload = validate_segment_bulk_validation_payload(_request_payload(request))
    except PydanticValidationError as exc:
        return _pydantic_error_response(exc)

    segment_ids = payload.segment_ids
    is_validated = payload.is_validated
    notes = payload.notes
    information_source_name = payload.information_source_name
    annotation_annotator = payload.annotator
    if notes:
        logger.info(f"Segment Validiert ${notes}")
    if not segment_ids:
        return Response(
            {"error": "segment_ids is required"}, status=status.HTTP_400_BAD_REQUEST
        )

    # optional per-segment timing info (seconds)
    segments_data = payload.timing_by_segment_id

    try:
        segments = list(
            LabelVideoSegment.objects.filter(
                pk__in=segment_ids, video_file=video
            ).select_related("state", "video_file")
        )

        if not segments:
            return Response(
                {"error": "No segments found with provided IDs for this video"},
                status=status.HTTP_404_NOT_FOUND,
            )

        updated_count = 0
        failed_ids: list[int] = []
        with transaction.atomic():
            for segment in segments:
                try:
                    # 1) optionally update times from payload
                    segment_pk = int(segment.pk)
                    data = segments_data.get(segment_pk)
                    if data is not None:
                        annotation_input = data.to_annotation_input(video_id=pk)
                        if annotation_input is not None:
                            segment_video = _segment_video_file(segment)
                            new_start = video_seconds_to_frame_number(
                                segment_video, annotation_input.start_time
                            )
                            new_end = video_seconds_to_frame_number(
                                segment_video, annotation_input.end_time
                            )
                            _validate_segment_frame_range(
                                new_start,
                                new_end,
                                video_file=segment_video,
                            )
                            segment.start_frame_number = new_start
                            segment.end_frame_number = new_end
                            _save_segment(
                                segment,
                                update_fields=[
                                    "start_frame_number",
                                    "end_frame_number",
                                ],
                            )
                    was_validated = bool(segment.state and segment.state.is_validated)
                    status_before = (
                        STATUS_VALIDATED
                        if (segment.state and was_validated)
                        else STATUS_UNVALIDATED
                    )

                    # 2) mark as validated + update information source + notes
                    segment.mark_validated(
                        is_validated=is_validated,
                        information_source_name=(
                            information_source_name if is_validated else str(None)
                        ),
                    )
                    segment_id = _segment_pk(segment)
                    updated_count += 1

                    #
                    def _log_after_commit(
                        segment_id: int = int(segment_id),
                        status_before: str = status_before,
                    ) -> None:
                        s = LabelVideoSegment.objects.select_related("state").get(
                            pk=segment_id
                        )

                        status_after = (
                            STATUS_VALIDATED
                            if (s.state and s.state.is_validated)
                            else STATUS_UNVALIDATED
                        )

                        record_operation(
                            cast(HttpRequest, request),
                            action=ACTION_SEGMENT_ANNOTATED,
                            resource_type="video_segment",
                            resource_id=_segment_pk(s),
                            status_before=status_before,
                            status_after=status_after,
                            meta={
                                "video_id": pk,
                                "bulk": True,
                                "information_source": information_source_name,
                                "annotator": annotation_annotator,
                            },
                        )

                    transaction.on_commit(_log_after_commit)
                    """status_after = STATUS_VALIDATED if is_validated else STATUS_UNVALIDATED

                    
                    record_operation(
                        request,
                        action=ACTION_SEGMENT_ANNOTATED,
                        resource_type="video_segment",
                        resource_id=segment.pk,
                        status_before=status_before,
                        status_after=status_after,
                        meta={
                            "video_id": pk,
                            "bulk": True,
                            "information_source": information_source_name,
                        },
                    )"""

                except Exception as e:
                    logger.error(f"Error validating segment {segment.pk}: {e}")
                    failed_ids.append(_segment_pk(segment))

            create_segment_update_lease_on_commit(video)

        logger.info(f"Bulk validated {updated_count} segments in video {pk}")
        response_status: int = status.HTTP_200_OK

        annotation_task_id: str | None = None
        post_processing_job: JobDispatchResult | None = None
        if is_validated and not failed_ids and updated_count == len(segment_ids):
            if _has_outside_cleanup_targets(video):
                mark_segment_annotations_pending_cleanup(video)
                try:
                    (
                        annotation_task_id,
                        expansion_completed,
                    ) = _dispatch_segment_annotation_expansion(
                        video_id=int(video.pk),
                        segment_ids=[int(segment_id) for segment_id in segment_ids],
                        information_source_name=information_source_name,
                        annotator=annotation_annotator,
                        dispatch_post_validation_rebuild=True,
                    )
                except Exception as exc:
                    logger.exception(
                        "Segment annotation expansion dispatch failed for video %s.",
                        video.pk,
                    )
                    return Response(
                        {
                            "error": "Segment annotation expansion dispatch failed.",
                            "detail": str(exc),
                            "video_id": pk,
                            **_segment_validation_state_payload(video),
                        },
                        status=status.HTTP_503_SERVICE_UNAVAILABLE,
                    )
                if expansion_completed:
                    annotation_integrity_errors = _segment_annotation_integrity_errors(
                        segments,
                        annotator=annotation_annotator,
                    )
                    if annotation_integrity_errors:
                        mark_segment_annotations_stale(video)
                        return Response(
                            {
                                "error": "Segment validation did not create complete frame annotations.",
                                "video_id": pk,
                                "updated_count": updated_count,
                                "requested_count": len(segment_ids),
                                "annotation_errors": annotation_integrity_errors,
                                **_segment_validation_state_payload(video),
                            },
                            status=status.HTTP_409_CONFLICT,
                        )
                    post_processing_job = dispatch_video_post_validation_rebuild(
                        video_id=video.pk,
                        only_validated=True,
                    )
                    response_status = _bulk_validation_response_status(
                        post_processing_job.status
                    )
                else:
                    response_status = status.HTTP_202_ACCEPTED
            else:
                mark_segment_annotations_pending_cleanup(video)
                try:
                    (
                        annotation_task_id,
                        expansion_completed,
                    ) = _dispatch_segment_annotation_expansion(
                        video_id=int(video.pk),
                        segment_ids=[int(segment_id) for segment_id in segment_ids],
                        information_source_name=information_source_name,
                        annotator=annotation_annotator,
                        dispatch_post_validation_rebuild=False,
                    )
                except Exception as exc:
                    logger.exception(
                        "Segment annotation expansion dispatch failed for video %s.",
                        video.pk,
                    )
                    return Response(
                        {
                            "error": "Segment annotation expansion dispatch failed.",
                            "detail": str(exc),
                            "video_id": pk,
                            **_segment_validation_state_payload(video),
                        },
                        status=status.HTTP_503_SERVICE_UNAVAILABLE,
                    )
                if expansion_completed:
                    annotation_integrity_errors = _segment_annotation_integrity_errors(
                        segments,
                        annotator=annotation_annotator,
                    )
                    if annotation_integrity_errors:
                        mark_segment_annotations_stale(video)
                        return Response(
                            {
                                "error": "Segment validation did not create complete frame annotations.",
                                "video_id": pk,
                                "updated_count": updated_count,
                                "requested_count": len(segment_ids),
                                "annotation_errors": annotation_integrity_errors,
                                **_segment_validation_state_payload(video),
                            },
                            status=status.HTTP_409_CONFLICT,
                        )
                    mark_segment_annotations_complete_without_cleanup(video)
                    post_processing_job = JobDispatchResult(
                        task_id="",
                        mode="noop",
                        status="noop",
                        video_id=int(video.pk),
                        history_id=None,
                        validation_status="completed",
                    )
                    response_status = status.HTTP_200_OK
                else:
                    response_status = status.HTTP_202_ACCEPTED

        response_data = {
            "message": f"Bulk validation completed. {updated_count} segments updated.",
            "updated_count": updated_count,
            "requested_count": len(segment_ids),
            "is_validated": is_validated,
            "video_id": pk,
            **_segment_validation_state_payload(video),
        }
        if annotation_task_id is not None:
            response_data["validation_status"] = "annotation_expansion_queued"
            response_data["annotation_expansion_task_id"] = annotation_task_id
        if post_processing_job is not None:
            response_data["validation_status"] = _validation_status_from_job(
                post_processing_job
            )
            response_data["post_processing_job"] = post_processing_job.to_dict()

        if failed_ids:
            response_data["failed_ids"] = failed_ids
            response_data["warning"] = (
                f"{len(failed_ids)} segments could not be validated"
            )
            response_status = status.HTTP_409_CONFLICT

        return Response(response_data, status=response_status)

    except Exception as e:
        logger.error(f"Error in bulk validation for video {pk}: {e}")
        return Response(
            {"error": f"Bulk validation failed: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["GET", "POST"])
@permission_classes([EnvironmentAwarePermission])
def video_segments_validation_status(request: Request, pk: int) -> Response:
    """
    Get or update validation status for all segments of a video.

    GET /api/media/videos/<pk>/segments/validation-status/
    Returns validation statistics for all segments.

    POST /api/media/videos/<pk>/segments/validation-status/
    Marks all segments (or filtered by label) as validated.

    Query Parameters (GET):
    - label_name: filter by label (optional)

    Request Body (POST, optional):
    {
      "label_name": "...",   // optional, only validate segments with this label
      "notes": "..."         // optional
    }

    Response (GET):
    {
      "video_id": 123,
      "total_segments": 10,
      "validated_count": 7,
      "unvalidated_count": 3,
      "validation_complete": false,
      "by_label": {...}
    }

    Response (POST):
    {
      "message": "Video segment validation completed",
      "video_id": 123,
      "total_segments": 10,
      "updated_count": 10,
      "failed_count": 0
    }
    """
    # Verify video exists
    video = get_object_or_404(VideoFile, pk=pk)

    if request.method == "GET":
        try:
            query = validate_segment_validation_status_payload(_request_query(request))
        except PydanticValidationError as exc:
            return _pydantic_error_response(exc, message="Invalid query parameters")
        label_name = query.label_name

        segments_query = LabelVideoSegment.objects.filter(
            video_file=video
        ).select_related("state", "label")

        if label_name:
            segments_query = segments_query.filter(label__name=label_name)

        segments = segments_query.all()
        total_count = segments.count()

        # Count validated segments
        validated_count = sum(bool(s.state and s.state.is_validated) for s in segments)

        # By label breakdown
        by_label: dict[str, dict[str, int]] = {}
        for segment in segments:
            label = _segment_label_name(segment) or "unknown"
            if label not in by_label:
                by_label[label] = {"total": 0, "validated": 0}
            by_label[label]["total"] += 1
            if segment.state and segment.state.is_validated:
                by_label[label]["validated"] += 1

        return Response(
            {
                "video_id": pk,
                "total_segments": total_count,
                "validated_count": validated_count,
                "unvalidated_count": total_count - validated_count,
                "validation_complete": validated_count == total_count
                and total_count > 0,
                "by_label": by_label,
                "label_filter": label_name,
            },
            status=status.HTTP_200_OK,
        )

    elif request.method == "POST":
        try:
            payload = validate_segment_validation_status_payload(
                _request_payload(request)
            )
        except PydanticValidationError as exc:
            return _pydantic_error_response(exc)

        # Mark all segments as validated
        label_name = payload.label_name
        segments_query = LabelVideoSegment.objects.filter(
            video_file=video
        ).select_related("state", "label")

        if label_name:
            segments_query = segments_query.filter(label__name=label_name)

        segments = segments_query.all()

        if not segments.exists():
            return Response(
                {
                    "message": "No segments found to validate",
                    "video_id": pk,
                    "updated_count": 0,
                },
                status=status.HTTP_200_OK,
            )

        segment_list = list(segments)
        segment_state_ids = [
            int(cast(Any, segment.state).pk)
            for segment in segment_list
            if getattr(segment, "state", None) is not None
        ]
        failed_count = len(segment_list) - len(segment_state_ids)

        with transaction.atomic():
            updated_count = LabelVideoSegmentState.objects.filter(
                pk__in=segment_state_ids
            ).update(is_validated=True)
            create_segment_update_lease_on_commit(video)

        logger.info(f"Completed validation for {updated_count} segments in video {pk}")
        logger.info("Queueing segment annotation expansion job")
        mark_segment_annotations_pending_cleanup(video)
        annotation_task_id = _dispatch_segment_annotation_expansion(
            video_id=int(video.pk),
            segment_ids=[_segment_pk(segment) for segment in segment_list],
            information_source_name="manual_annotation",
            annotator=None,
            dispatch_post_validation_rebuild=True,
        )
        return Response(
            {
                "message": f"Video segment validation completed for video {pk}",
                "video_id": pk,
                "total_segments": len(segment_list),
                "updated_count": updated_count,
                "failed_count": failed_count,
                "label_filter": label_name,
                "validation_status": "annotation_expansion_queued",
                "annotation_expansion_task_id": annotation_task_id,
            },
            status=status.HTTP_202_ACCEPTED,
        )
    return Response(
        {"error": f"Method {request.method} not allowed"},
        status=status.HTTP_405_METHOD_NOT_ALLOWED,
    )


@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission])
def ensure_segment_annotations_for_video(request: Request, pk: int) -> Response:
    """
    Trigger idempotent annotation regeneration for segments attached to a single video.

    Body (optional):
    {
      "segment_ids": [1,2,3],
      "information_source_name": "manual_annotation"
    }
    """
    try:
        payload = validate_segment_annotation_ensure_payload(
            _request_payload(request),
            default_information_source_name="manual_annotation",
        )
    except PydanticValidationError as exc:
        return _pydantic_error_response(exc)

    segment_ids = payload.segment_ids
    info_source = payload.information_source_name

    try:
        stats = ensure_segment_annotations(
            video_ids=None if segment_ids else [pk],
            segment_ids=segment_ids,
            information_source_name=info_source,
            commit=True,
        )
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    return Response(
        {
            "task_id": str(uuid.uuid4()),
            "status": "accepted",
            "video_id": pk,
            "stats": stats,
        },
        status=status.HTTP_202_ACCEPTED,
    )


@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission])
def ensure_segment_annotations_bulk(request: Request) -> Response:
    """
    Trigger annotation regeneration for multiple videos/segments.

    Body:
    {
      "video_ids": [1,2],
      "segment_ids": [10,11],
      "information_source_name": "manual_annotation"
    }
    """
    try:
        payload = validate_segment_annotation_ensure_payload(
            _request_payload(request),
            default_information_source_name="manual_annotation",
        )
    except PydanticValidationError as exc:
        return _pydantic_error_response(exc)

    video_ids = payload.video_ids
    segment_ids = payload.segment_ids
    info_source = payload.information_source_name

    if not video_ids and not segment_ids:
        return Response(
            {"detail": "Provide video_ids or segment_ids"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    stats = ensure_segment_annotations(
        video_ids=video_ids,
        segment_ids=segment_ids,
        information_source_name=info_source,
        commit=True,
    )

    return Response(
        {
            "task_id": str(uuid.uuid4()),
            "status": "accepted",
            "stats": stats,
            "video_ids": video_ids,
            "segment_ids": segment_ids,
        },
        status=status.HTTP_202_ACCEPTED,
    )


@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission])
def ensure_prediction_segment_annotations_for_video(
    request: Request,
    pk: int,
) -> Response:
    """
    Trigger idempotent annotation generation for AI/prediction-based segments
    attached to a single video, writing to a dedicated information source.

    Body (optional):
    {
      "segment_ids": [1,2,3],
      "information_source_name": "prediction_annotation"
    }
    """
    try:
        payload = validate_segment_annotation_ensure_payload(
            _request_payload(request),
            default_information_source_name="prediction_annotation",
        )
    except PydanticValidationError as exc:
        return _pydantic_error_response(exc)

    segment_ids = payload.segment_ids
    info_source = payload.information_source_name

    try:
        stats = ensure_prediction_segment_annotations(
            video_ids=None if segment_ids else [pk],
            segment_ids=segment_ids,
            information_source_name=info_source,
            commit=True,
        )
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    return Response(
        {
            "task_id": str(uuid.uuid4()),
            "status": "accepted",
            "video_id": pk,
            "stats": stats,
        },
        status=status.HTTP_202_ACCEPTED,
    )


@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission])
def ensure_prediction_segment_annotations_bulk(request: Request) -> Response:
    """
    Trigger annotation generation for AI/prediction-based segments for multiple
    videos/segments, using a dedicated information source.

    Body:
    {
      "video_ids": [1,2],
      "segment_ids": [10,11],
      "information_source_name": "prediction_annotation"
    }
    """
    try:
        payload = validate_segment_annotation_ensure_payload(
            _request_payload(request),
            default_information_source_name="prediction_annotation",
        )
    except PydanticValidationError as exc:
        return _pydantic_error_response(exc)

    video_ids = payload.video_ids
    segment_ids = payload.segment_ids
    info_source = payload.information_source_name

    if not video_ids and not segment_ids:
        return Response(
            {"detail": "Provide video_ids or segment_ids"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    stats = ensure_prediction_segment_annotations(
        video_ids=video_ids,
        segment_ids=segment_ids,
        information_source_name=info_source,
        commit=True,
    )

    return Response(
        {
            "task_id": str(uuid.uuid4()),
            "status": "accepted",
            "stats": stats,
            "video_ids": video_ids,
            "segment_ids": segment_ids,
        },
        status=status.HTTP_202_ACCEPTED,
    )

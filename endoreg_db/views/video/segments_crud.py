"""
Modern Media Framework - Video Segments Views
Migrated from legacy label_video_segment views (October 14, 2025)

Provides RESTful endpoints for video segment management:
- Collection: GET/POST /api/media/videos/segments/
- Detail: GET/PATCH/DELETE /api/media/videos/<pk>/segments/<segment_id>/
- Video-specific: GET/POST /api/media/videos/<pk>/segments/
"""

import logging
import uuid

from django.db import models, transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from endoreg_db.models import (
    ImageClassificationAnnotation,
    InformationSource,
    Label,
    LabelVideoSegment,
    VideoFile,
)
from endoreg_db.services.segment_annotations import (
    ensure_prediction_segment_annotations,
    ensure_segment_annotations,
)
from endoreg_db.services.video_post_validation_jobs import (
    dispatch_video_post_validation_rebuild,
)
from endoreg_db.serializers.label_video_segment.label_video_segment import (
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


def _prediction_segment_query() -> Q:
    return Q(prediction_meta__isnull=False) | Q(source__name="prediction")


def _filter_segments_by_origin(queryset, source_kind: str | None):
    normalized = str(source_kind or "all").strip().lower()
    if normalized == "prediction":
        return queryset.filter(_prediction_segment_query()).distinct()
    if normalized == "manual":
        return queryset.exclude(_prediction_segment_query()).distinct()
    return queryset


def _segment_annotation_filters(
    *,
    video: VideoFile,
    start_frame_number: int,
    end_frame_number: int,
    label: Label | None,
    information_source_id: int | None,
    model_meta_id: int | None,
) -> dict:
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


def _delete_frame_annotations_for_segment(
    *,
    video: VideoFile,
    start_frame_number: int,
    end_frame_number: int,
    label: Label | None,
    information_source_id: int | None,
    model_meta_id: int | None,
) -> int:
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


def _sync_frame_annotations(
    *,
    segment: LabelVideoSegment,
    old_snapshot: dict | None = None,
) -> None:
    if old_snapshot:
        _delete_frame_annotations_for_segment(
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
    model_meta_id = model_meta.pk if model_meta else None

    frames_queryset = segment.get_frames().only("id")
    if not isinstance(frames_queryset, models.QuerySet):
        return

    existing_frame_ids = set(
        ImageClassificationAnnotation.objects.filter(
            frame_id__in=frames_queryset.values("id"),
            label=segment.label,
            information_source_id=info_source_id,
            model_meta_id=model_meta_id,
        ).values_list("frame_id", flat=True)
    )

    annotations_to_create = []
    for frame in frames_queryset.exclude(id__in=existing_frame_ids).iterator():
        annotations_to_create.append(
            ImageClassificationAnnotation(
                frame=frame,
                label=segment.label,
                value=True,
                information_source_id=info_source_id,
                model_meta_id=model_meta_id,
            )
        )

    if annotations_to_create:
        ImageClassificationAnnotation.objects.bulk_create(
            annotations_to_create, ignore_conflicts=True
        )


def _normalize_int_list(value):
    if value is None:
        return None
    if isinstance(value, list):
        return [int(item) for item in value if item is not None]
    return [int(value)]


def _query_param_as_bool(value, *, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
def video_segments_stats(request):
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
def video_segments_collection(request):
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

        with transaction.atomic():
            serializer = LabelVideoSegmentSerializer(data=request.data)
            if serializer.is_valid():
                try:
                    segment = serializer.save()
                    _sync_frame_annotations(segment=segment)
                    logger.info(f"Successfully created video segment {segment.pk}")
                    return Response(
                        LabelVideoSegmentSerializer(segment).data,
                        status=status.HTTP_201_CREATED,
                    )
                except Exception as e:
                    logger.error(f"Error creating video segment: {str(e)}")
                    return Response(
                        {"error": f"Failed to create segment: {str(e)}"},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    )
            else:
                details = serializer.errors
                logger.warning(f"Invalid data for video segment creation: {details}")
                return Response(
                    {"error": "Invalid data", "details": details},
                    status=status.HTTP_400_BAD_REQUEST,
                )

    elif request.method == "GET":
        # Optional filtering by video_id
        video_id = request.GET.get("video_id")
        label_id = request.GET.get("label_id")
        source_kind = request.GET.get("source_kind")
        include_annotation_payload = _query_param_as_bool(
            request.GET.get("include_annotation_payload"),
            default=False,
        )

        queryset = LabelVideoSegment.objects.select_related("video_file", "label").all()

        if video_id:
            try:
                video = VideoFile.objects.get(id=video_id)
                queryset = queryset.filter(video_file=video)
            except VideoFile.DoesNotExist:
                return Response(
                    {"error": f"Video with id {video_id} not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )

        if label_id:
            try:
                label = Label.objects.get(id=label_id)
                queryset = queryset.filter(label=label)
            except Label.DoesNotExist:
                return Response(
                    {"error": f"Label with id {label_id} not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )

        queryset = _filter_segments_by_origin(queryset, source_kind)

        # Order by video and start time for consistent results
        segments = queryset.order_by("video_file__id", "start_frame_number")
        serializer = LabelVideoSegmentSerializer(
            segments,
            many=True,
            context={
                "request": request,
                "include_annotation_payload": include_annotation_payload,
            },
        )
        return Response(serializer.data)


@api_view(["GET", "POST"])
@permission_classes([EnvironmentAwarePermission])
def video_segments_by_video(request, pk):
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
        # This duplicates video_segments_by_pk functionality
        # We keep both for compatibility during migration
        label_name = request.GET.get("label")
        source_kind = request.GET.get("source_kind")
        include_annotation_payload = _query_param_as_bool(
            request.GET.get("include_annotation_payload"),
            default=False,
        )

        queryset = LabelVideoSegment.objects.filter(video_file=video).select_related(
            "video_file", "label"
        )

        if label_name:
            try:
                label = Label.objects.get(name=label_name)
                queryset = queryset.filter(label=label)
            except Label.DoesNotExist:
                return Response(
                    {"error": f'Label "{label_name}" not found'},
                    status=status.HTTP_404_NOT_FOUND,
                )

        queryset = _filter_segments_by_origin(queryset, source_kind)

        segments = queryset.order_by("start_frame_number")
        serializer = LabelVideoSegmentSerializer(
            segments,
            many=True,
            context={
                "request": request,
                "include_annotation_payload": include_annotation_payload,
            },
        )
        return Response(serializer.data)

    elif request.method == "POST":
        logger.info(f"Creating new segment for video {pk} with data: {request.data}")

        # Automatically set video_id to pk
        data = request.data.copy()
        data["video_id"] = pk

        with transaction.atomic():
            serializer = LabelVideoSegmentSerializer(data=data)
            if serializer.is_valid():
                try:
                    segment = serializer.save()
                    _sync_frame_annotations(segment=segment)
                    logger.info(
                        f"Successfully created segment {segment.pk} for video {pk}"
                    )
                    return Response(
                        LabelVideoSegmentSerializer(segment).data,
                        status=status.HTTP_201_CREATED,
                    )
                except Exception as e:
                    logger.error(f"Error creating segment for video {pk}: {str(e)}")
                    return Response(
                        {"error": f"Failed to create segment: {str(e)}"},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    )
            else:
                logger.warning(
                    f"Invalid data for segment creation: {serializer.errors}"
                )
                return Response(
                    {"error": "Invalid data", "details": serializer.errors},
                    status=status.HTTP_400_BAD_REQUEST,
                )


@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission])
def import_prediction_segments_to_manual(request, pk: int):
    """
    Replace or extend the manual segment layer for a video using a caller-supplied
    segment list, typically loaded from pipe-1 predictions and adjusted in the UI.

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
    raw_segments = request.data.get("segments")
    replace_existing = bool(
        request.data.get("replace_existing", request.data.get("replaceExisting", True))
    )

    if not isinstance(raw_segments, list) or len(raw_segments) == 0:
        return Response(
            {"error": "segments must be a non-empty list"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    manual_source, _ = InformationSource.objects.get_or_create(
        name="manual_annotation",
        defaults={"description": "Manually created label segments via web interface"},
    )

    created_segments: list[LabelVideoSegment] = []
    with transaction.atomic():
        if replace_existing:
            manual_segments = LabelVideoSegment.objects.filter(
                video_file=video
            ).exclude(_prediction_segment_query())
            for segment in manual_segments.iterator():
                if segment.label is not None:
                    delete_model_meta = segment.get_model_meta()
                    _delete_frame_annotations_for_segment(
                        video=segment.video_file,
                        start_frame_number=segment.start_frame_number,
                        end_frame_number=segment.end_frame_number,
                        label=segment.label,
                        information_source_id=segment.source_id,
                        model_meta_id=(
                            delete_model_meta.pk if delete_model_meta else None
                        ),
                    )
                segment.delete()

        for idx, item in enumerate(raw_segments):
            if not isinstance(item, dict):
                return Response(
                    {"error": f"segments[{idx}] must be an object"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            payload = {
                "video_id": pk,
                "label_name": item.get("label_name") or item.get("label"),
                "start_time": item.get("start_time"),
                "end_time": item.get("end_time"),
                "export_segment": bool(item.get("export_segment", False)),
            }
            serializer = LabelVideoSegmentSerializer(data=payload)
            if not serializer.is_valid():
                return Response(
                    {
                        "error": "Invalid segment import payload",
                        "details": serializer.errors,
                        "segment_index": idx,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            segment = serializer.save()
            if segment.source_id != manual_source.id:
                segment.source = manual_source
                segment.save(update_fields=["source"])
            _sync_frame_annotations(segment=segment)
            created_segments.append(segment)

    response_serializer = LabelVideoSegmentTimelineSerializer(
        created_segments,
        many=True,
    )
    return Response(
        {
            "message": "Prediction segments imported to manual annotations.",
            "created_count": len(created_segments),
            "replaced_existing": replace_existing,
            "segments": response_serializer.data,
        },
        status=status.HTTP_200_OK,
    )


@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([EnvironmentAwarePermission])
def video_segment_detail(request, pk, segment_id):
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
        return Response(serializer.data)

    elif request.method == "PATCH":
        logger.info(
            f"Updating segment {segment_id} for video {pk} with data: {request.data}"
        )

        with transaction.atomic():
            old_model_meta = segment.get_model_meta()
            old_snapshot = {
                "video": segment.video_file,
                "start_frame_number": segment.start_frame_number,
                "end_frame_number": segment.end_frame_number,
                "label": segment.label,
                "information_source_id": segment.source_id,
                "model_meta_id": old_model_meta.pk if old_model_meta else None,
            }
            serializer = LabelVideoSegmentSerializer(
                segment, data=request.data, partial=True
            )
            if serializer.is_valid():
                try:
                    segment = serializer.save()
                    _sync_frame_annotations(
                        segment=segment,
                        old_snapshot=old_snapshot,
                    )
                    logger.info(f"Successfully updated segment {segment_id}")
                    return Response(LabelVideoSegmentSerializer(segment).data)
                except Exception as e:
                    logger.error(f"Error updating segment {segment_id}: {str(e)}")
                    return Response(
                        {"error": f"Failed to update segment: {str(e)}"},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    )
            else:
                logger.warning(f"Invalid data for segment update: {serializer.errors}")
                return Response(
                    {"error": "Invalid data", "details": serializer.errors},
                    status=status.HTTP_400_BAD_REQUEST,
                )

    elif request.method == "DELETE":
        logger.info(f"Deleting segment {segment_id} from video {pk}")
        try:
            with transaction.atomic():
                if segment.label is not None:
                    delete_model_meta = segment.get_model_meta()
                    _delete_frame_annotations_for_segment(
                        video=segment.video_file,
                        start_frame_number=segment.start_frame_number,
                        end_frame_number=segment.end_frame_number,
                        label=segment.label,
                        information_source_id=segment.source_id,
                        model_meta_id=(
                            delete_model_meta.pk if delete_model_meta else None
                        ),
                    )
                segment.delete()
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


# ============================================================================
# VIDEO SEGMENT VALIDATION ENDPOINTS (Modern Framework)
# Migrated from /api/label-video-segment/*/validate/ (October 14, 2025)
# ============================================================================


@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission])
def video_segment_validate(request, pk: int, segment_id: int):
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
        is_validated = request.data.get("is_validated", True)
        information_source_name = request.data.get(
            "information_source_name", "manual_annotation"
        )

        # Optional: update times (seconds) before validation
        start_time = request.data.get("start_time")
        end_time = request.data.get("end_time")
        fps_value = 0.0
        if start_time is not None and end_time is not None:
            fps_value = segment.video_file.get_fps() or 0

        with transaction.atomic():
            if start_time is not None and end_time is not None:
                if fps_value > 0:
                    new_start = int(round(float(start_time) * fps_value))
                    new_end = int(round(float(end_time) * fps_value))
                    LabelVideoSegment.validate_frame_range(
                        new_start, new_end, video_file=segment.video_file
                    )
                    segment.start_frame_number = new_start
                    segment.end_frame_number = new_end
                    segment.save(
                        update_fields=["start_frame_number", "end_frame_number"]
                    )

            segment.mark_validated(
                is_validated=is_validated,
                information_source_name=information_source_name,
            )
            try:
                segment.generate_annotations()
                segment_id = segment.pk
            except Exception as exc:
                logger.warning(
                    "Failed to generate annotations while validating segment %s: %s",
                    segment.pk,
                    exc,
                )

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
                    "video_id": video.pk,
                    "label": segment.label.name if segment.label else None,
                    "information_source": information_source_name,
                },
            )"""
        post_processing_job = dispatch_video_post_validation_rebuild(video_id=video.pk)

        logger.info(f"Validated segment {segment_id} in video {pk}: {is_validated}")

        return Response(
            {
                "message": f"Segment {segment_id} validation status updated",
                "segment_id": segment_id,
                "is_validated": is_validated,
                "label": segment.label.name if segment.label else None,
                "video_id": video.pk,
                "start_frame": segment.start_frame_number,
                "end_frame": segment.end_frame_number,
                "post_processing_job": post_processing_job.to_dict(),
            },
            status=status.HTTP_200_OK,
        )

    except Exception as e:
        logger.error(f"Error validating segment {segment_id} in video {pk}: {e}")
        return Response(
            {"error": f"Validation failed: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


# TODO Pass user based information source to backend. This is the endpoint currently used by the VideoExamination endpoint
@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission])
def video_segments_validate_bulk(request, pk: int):
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

    segment_ids = request.data.get("segment_ids", [])
    is_validated = request.data.get("is_validated", True)
    notes = request.data.get("notes", "")
    information_source_name = request.data.get(
        "information_source_name", "manual_annotation"
    )
    if notes:
        logger.info(f"Segment Validiert ${notes}")
    if not segment_ids:
        return Response(
            {"error": "segment_ids is required"}, status=status.HTTP_400_BAD_REQUEST
        )

    # optional per-segment timing info (seconds)
    segments_data_list = request.data.get("segments", []) or []
    segments_data = {int(s["id"]): s for s in segments_data_list if "id" in s}

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

        fps_by_segment_id = {}
        for segment in segments:
            data = segments_data.get(segment.pk)
            if data is None:
                continue
            start_time = data.get("start_time")
            end_time = data.get("end_time")
            if start_time is not None and end_time is not None:
                fps_by_segment_id[segment.pk] = segment.video_file.get_fps() or 0

        updated_count = 0
        failed_ids = []

        with transaction.atomic():
            for segment in segments:
                try:
                    # 1) optionally update times from payload
                    data = segments_data.get(segment.pk)
                    if data is not None:
                        start_time = data.get("start_time")
                        end_time = data.get("end_time")
                        if start_time is not None and end_time is not None:
                            fps_value = fps_by_segment_id.get(segment.pk, 0)
                            if fps_value > 0:
                                new_start = int(round(float(start_time) * fps_value))
                                new_end = int(round(float(end_time) * fps_value))
                                LabelVideoSegment.validate_frame_range(
                                    new_start, new_end, video_file=segment.video_file
                                )
                                segment.start_frame_number = new_start
                                segment.end_frame_number = new_end
                                segment.save(
                                    update_fields=[
                                        "start_frame_number",
                                        "end_frame_number",
                                    ]
                                )

                    status_before = (
                        STATUS_VALIDATED
                        if (segment.state and segment.state.is_validated)
                        else STATUS_UNVALIDATED
                    )

                    # 2) mark as validated + update information source + notes
                    segment.mark_validated(
                        is_validated=is_validated,
                        information_source_name=str(information_source_name)
                        if is_validated
                        else str(None),
                    )
                    try:
                        segment.generate_annotations()
                        segment_id = segment.pk
                    except Exception as exc:
                        logger.warning(
                            "Failed to generate annotations while bulk validating segment %s: %s",
                            segment.pk,
                            exc,
                        )
                    updated_count += 1

                    #
                    def _log_after_commit(segment_id=segment_id):
                        s = LabelVideoSegment.objects.select_related("state").get(
                            pk=segment_id
                        )

                        status_after = (
                            STATUS_VALIDATED
                            if (s.state and s.state.is_validated)
                            else STATUS_UNVALIDATED
                        )

                        record_operation(
                            request,
                            action=ACTION_SEGMENT_ANNOTATED,
                            resource_type="video_segment",
                            resource_id=s.pk,
                            status_before=status_before,
                            status_after=status_after,
                            meta={
                                "video_id": pk,
                                "bulk": True,
                                "information_source": information_source_name,
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
                    failed_ids.append(segment.pk)

        logger.info(f"Bulk validated {updated_count} segments in video {pk}")
        post_processing_job = None

        if is_validated and not failed_ids and updated_count == len(segment_ids):
            state = video.get_or_create_state()
            state.segment_annotations_created = True
            state.segment_annotations_validated = True
            state.save(
                update_fields=[
                    "segment_annotations_created",
                    "segment_annotations_validated",
                    "date_modified",
                ]
            )
            post_processing_job = dispatch_video_post_validation_rebuild(
                video_id=video.pk
            )

        response_data = {
            "message": f"Bulk validation completed. {updated_count} segments updated.",
            "updated_count": updated_count,
            "requested_count": len(segment_ids),
            "is_validated": is_validated,
            "video_id": pk,
        }
        if post_processing_job is not None:
            response_data["post_processing_job"] = post_processing_job.to_dict()

        if failed_ids:
            response_data["failed_ids"] = failed_ids
            response_data["warning"] = (
                f"{len(failed_ids)} segments could not be validated"
            )

        return Response(response_data, status=status.HTTP_200_OK)

    except Exception as e:
        logger.error(f"Error in bulk validation for video {pk}: {e}")
        return Response(
            {"error": f"Bulk validation failed: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["GET", "POST"])
@permission_classes([EnvironmentAwarePermission])
def video_segments_validation_status(request, pk: int):
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
        # Get validation status
        label_name = request.query_params.get("label_name")

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
        by_label = {}
        for segment in segments:
            label = segment.label.name if segment.label else "unknown"
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
        # Mark all segments as validated
        label_name = request.data.get("label_name")
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

        updated_count = 0
        failed_count = 0

        with transaction.atomic():
            for segment in segments:
                try:
                    if segment.state:
                        segment.state.is_validated = True
                        segment.state.save()
                        updated_count += 1
                    else:
                        failed_count += 1
                except Exception as e:
                    logger.error(f"Error validating segment {segment.pk}: {e}")
                    failed_count += 1

        logger.info(f"Completed validation for {updated_count} segments in video {pk}")
        logger.info("Queueing outside-frame rebuild job")
        post_processing_job = dispatch_video_post_validation_rebuild(video_id=video.pk)
        return Response(
            {
                "message": f"Video segment validation completed for video {pk}",
                "video_id": pk,
                "total_segments": len(segments),
                "updated_count": updated_count,
                "failed_count": failed_count,
                "label_filter": label_name,
                "post_processing_job": post_processing_job.to_dict(),
            },
            status=status.HTTP_200_OK,
        )


@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission])
def ensure_segment_annotations_for_video(request, pk: int):
    """
    Trigger idempotent annotation regeneration for segments attached to a single video.

    Body (optional):
    {
      "segment_ids": [1,2,3],
      "information_source_name": "manual_annotation"
    }
    """
    segment_ids = _normalize_int_list(request.data.get("segment_ids"))
    info_source = request.data.get("information_source_name", "manual_annotation")

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
def ensure_segment_annotations_bulk(request):
    """
    Trigger annotation regeneration for multiple videos/segments.

    Body:
    {
      "video_ids": [1,2],
      "segment_ids": [10,11],
      "information_source_name": "manual_annotation"
    }
    """
    video_ids = _normalize_int_list(request.data.get("video_ids"))
    segment_ids = _normalize_int_list(request.data.get("segment_ids"))
    info_source = request.data.get("information_source_name", "manual_annotation")

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
def ensure_prediction_segment_annotations_for_video(request, pk: int):
    """
    Trigger idempotent annotation generation for AI/prediction-based segments
    attached to a single video, writing to a dedicated information source.

    Body (optional):
    {
      "segment_ids": [1,2,3],
      "information_source_name": "prediction_annotation"
    }
    """
    segment_ids = _normalize_int_list(request.data.get("segment_ids"))
    info_source = request.data.get("information_source_name", "prediction_annotation")

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
def ensure_prediction_segment_annotations_bulk(request):
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
    video_ids = _normalize_int_list(request.data.get("video_ids"))
    segment_ids = _normalize_int_list(request.data.get("segment_ids"))
    info_source = request.data.get("information_source_name", "prediction_annotation")

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

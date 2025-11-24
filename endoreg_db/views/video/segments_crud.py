"""
Modern Media Framework - Video Segments Views
Migrated from legacy label_video_segment views (October 14, 2025)

Provides RESTful endpoints for video segment management:
- Collection: GET/POST /api/media/videos/segments/
- Detail: GET/PATCH/DELETE /api/media/videos/<pk>/segments/<segment_id>/
- Video-specific: GET/POST /api/media/videos/<pk>/segments/
"""

import logging

from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from endoreg_db.models import Label, LabelVideoSegment, VideoFile
from endoreg_db.serializers.label_video_segment.label_video_segment import LabelVideoSegmentSerializer
from endoreg_db.utils.permissions import EnvironmentAwarePermission

logger = logging.getLogger(__name__)


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
            "by_label": {item["label__name"]: item["count"] for item in label_counts if item["label__name"]},
        }

        return Response(stats, status=status.HTTP_200_OK)

    except Exception as e:
        logger.error(f"Error fetching video segment stats: {e}")
        return Response({"error": "Failed to fetch segment statistics"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET", "POST"])
@permission_classes([EnvironmentAwarePermission])
def video_segments_collection(request):
    """
    Collection endpoint for all video segments across all videos.

    GET /api/media/videos/segments/
    - Lists all segments, optionally filtered by video_id and/or label_id
    - Query params: video_id, label_id

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
                    logger.info(f"Successfully created video segment {segment.pk}")
                    return Response(LabelVideoSegmentSerializer(segment).data, status=status.HTTP_201_CREATED)
                except Exception as e:
                    logger.error(f"Error creating video segment: {str(e)}")
                    return Response({"error": f"Failed to create segment: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            else:
                logger.warning(f"Invalid data for video segment creation: {serializer.errors}")
                return Response({"error": "Invalid data", "details": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

    elif request.method == "GET":
        # Optional filtering by video_id
        video_id = request.GET.get("video_id")
        label_id = request.GET.get("label_id")

        queryset = LabelVideoSegment.objects.all()

        if video_id:
            try:
                video = VideoFile.objects.get(id=video_id)
                queryset = queryset.filter(video_file=video)
            except VideoFile.DoesNotExist:
                return Response({"error": f"Video with id {video_id} not found"}, status=status.HTTP_404_NOT_FOUND)

        if label_id:
            try:
                label = Label.objects.get(id=label_id)
                queryset = queryset.filter(label=label)
            except Label.DoesNotExist:
                return Response({"error": f"Label with id {label_id} not found"}, status=status.HTTP_404_NOT_FOUND)

        # Order by video and start time for consistent results
        segments = queryset.order_by("video_file__id", "start_frame_number")
        serializer = LabelVideoSegmentSerializer(segments, many=True)
        return Response(serializer.data)


@api_view(["GET", "POST"])
@permission_classes([EnvironmentAwarePermission])
def video_segments_by_video(request, pk):
    """
    Video-specific segments endpoint.

    GET /api/media/videos/<pk>/segments/
    - Lists all segments for a specific video
    - Query params: label (label name filter)
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

        queryset = LabelVideoSegment.objects.filter(video_file=video)

        if label_name:
            try:
                label = Label.objects.get(name=label_name)
                queryset = queryset.filter(label=label)
            except Label.DoesNotExist:
                return Response({"error": f'Label "{label_name}" not found'}, status=status.HTTP_404_NOT_FOUND)

        segments = queryset.order_by("start_frame_number")
        serializer = LabelVideoSegmentSerializer(segments, many=True)
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
                    logger.info(f"Successfully created segment {segment.pk} for video {pk}")
                    return Response(LabelVideoSegmentSerializer(segment).data, status=status.HTTP_201_CREATED)
                except Exception as e:
                    logger.error(f"Error creating segment for video {pk}: {str(e)}")
                    return Response({"error": f"Failed to create segment: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            else:
                logger.warning(f"Invalid data for segment creation: {serializer.errors}")
                return Response({"error": "Invalid data", "details": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)


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
        logger.info(f"Updating segment {segment_id} for video {pk} with data: {request.data}")

        with transaction.atomic():
            serializer = LabelVideoSegmentSerializer(segment, data=request.data, partial=True)
            if serializer.is_valid():
                try:
                    segment = serializer.save()
                    logger.info(f"Successfully updated segment {segment_id}")
                    return Response(LabelVideoSegmentSerializer(segment).data)
                except Exception as e:
                    logger.error(f"Error updating segment {segment_id}: {str(e)}")
                    return Response({"error": f"Failed to update segment: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            else:
                logger.warning(f"Invalid data for segment update: {serializer.errors}")
                return Response({"error": "Invalid data", "details": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

    elif request.method == "DELETE":
        logger.info(f"Deleting segment {segment_id} from video {pk}")
        try:
            with transaction.atomic():
                segment.delete()
                logger.info(f"Successfully deleted segment {segment_id}")
                return Response({"message": f"Segment {segment_id} deleted successfully"}, status=status.HTTP_204_NO_CONTENT)
        except Exception as e:
            logger.error(f"Error deleting segment {segment_id}: {str(e)}")
            return Response({"error": f"Failed to delete segment: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


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

    # Get segment and verify it belongs to this video
    segment = get_object_or_404(LabelVideoSegment.objects.select_related("state", "video_file", "label"), pk=segment_id, video_file=video)

    try:
        # Validation status from request (default: True)
        is_validated = request.data.get("is_validated", True)
        notes = request.data.get("notes", "")

        # Get or create state object
        if not hasattr(segment, "state") or segment.state is None:
            return Response({"error": "Segment has no state object. Cannot validate."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # Update state
        with transaction.atomic():
            segment.state.is_validated = is_validated
            segment.state.save()

        logger.info(f"Validated segment {segment_id} in video {pk}: {is_validated}")

        return Response(
            {
                "message": f"Segment {segment_id} validation status updated",
                "segment_id": segment_id,
                "is_validated": is_validated,
                "label": segment.label.name if segment.label else None,
                "video_id": video.id,
                "start_frame": segment.start_frame_number,
                "end_frame": segment.end_frame_number,
            },
            status=status.HTTP_200_OK,
        )

    except Exception as e:
        logger.error(f"Error validating segment {segment_id} in video {pk}: {e}")
        return Response({"error": f"Validation failed: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission])
def video_segments_validate_bulk(request, pk: int):
    """
    Validate multiple video segments at once.

    POST /api/media/videos/<pk>/segments/validate-bulk/

    Validates multiple LabelVideoSegments simultaneously.
    Useful for batch validation after review.

    Request Body:
    {
      "segment_ids": [1, 2, 3, ...],
      "is_validated": true,  // optional, default true
      "notes": "..."         // optional, applies to all segments
    }

    Response:
    {
      "message": "Bulk validation completed. 3 segments updated.",
      "updated_count": 3,
      "requested_count": 3,
      "is_validated": true,
      "failed_ids": []  // only present if some failed
    }
    """
    # Verify video exists
    video = get_object_or_404(VideoFile, pk=pk)

    segment_ids = request.data.get("segment_ids", [])
    is_validated = request.data.get("is_validated", True)
    notes = request.data.get("notes", "")

    if not segment_ids:
        return Response({"error": "segment_ids is required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        # Get all segments for this video only
        segments = LabelVideoSegment.objects.filter(pk__in=segment_ids, video_file=video).select_related("state")

        if not segments.exists():
            return Response({"error": "No segments found with provided IDs for this video"}, status=status.HTTP_404_NOT_FOUND)

        updated_count = 0
        failed_ids = []

        with transaction.atomic():
            for segment in segments:
                try:
                    if segment.state:
                        segment.state.is_validated = is_validated
                        if notes and hasattr(segment.state, "validation_notes"):
                            segment.state.validation_notes = notes
                        segment.state.save()
                        updated_count += 1
                    else:
                        failed_ids.append(segment.id)
                except Exception as e:
                    logger.error(f"Error validating segment {segment.id}: {e}")
                    failed_ids.append(segment.id)

        logger.info(f"Bulk validated {updated_count} segments in video {pk}")

        response_data = {
            "message": f"Bulk validation completed. {updated_count} segments updated.",
            "updated_count": updated_count,
            "requested_count": len(segment_ids),
            "is_validated": is_validated,
            "video_id": pk,
        }

        if failed_ids:
            response_data["failed_ids"] = failed_ids
            response_data["warning"] = f"{len(failed_ids)} segments could not be validated"

        return Response(response_data, status=status.HTTP_200_OK)

    except Exception as e:
        logger.error(f"Error in bulk validation for video {pk}: {e}")
        return Response({"error": f"Bulk validation failed: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


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

        segments_query = LabelVideoSegment.objects.filter(video_file=video).select_related("state", "label")

        if label_name:
            segments_query = segments_query.filter(label__name=label_name)

        segments = segments_query.all()
        total_count = segments.count()

        # Count validated segments
        validated_count = sum(1 for s in segments if s.state and s.state.is_validated)

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
                "validation_complete": validated_count == total_count and total_count > 0,
                "by_label": by_label,
                "label_filter": label_name,
            },
            status=status.HTTP_200_OK,
        )

    elif request.method == "POST":
        # Mark all segments as validated
        label_name = request.data.get("label_name")
        notes = request.data.get("notes", "")

        segments_query = LabelVideoSegment.objects.filter(video_file=video).select_related("state", "label")

        if label_name:
            segments_query = segments_query.filter(label__name=label_name)

        segments = segments_query.all()

        if not segments.exists():
            return Response({"message": "No segments found to validate", "video_id": pk, "updated_count": 0}, status=status.HTTP_200_OK)

        updated_count = 0
        failed_count = 0

        with transaction.atomic():
            for segment in segments:
                try:
                    if segment.state:
                        segment.state.is_validated = True
                        if notes and hasattr(segment.state, "validation_notes"):
                            segment.state.validation_notes = notes
                        segment.state.save()
                        updated_count += 1
                    else:
                        failed_count += 1
                except Exception as e:
                    logger.error(f"Error validating segment {segment.id}: {e}")
                    failed_count += 1

        logger.info(f"Completed validation for {updated_count} segments in video {pk}")
        logger.info(f"Removing Outside Segments")
        video_file.label_video_segments.filter(video_file=video, label="outside", state__is_validated=False).delete()
        return Response(
            {
                "message": f"Video segment validation completed for video {pk}",
                "video_id": pk,
                "total_segments": len(segments),
                "updated_count": updated_count,
                "failed_count": failed_count,
                "label_filter": label_name,
            },
            status=status.HTTP_200_OK,
        )

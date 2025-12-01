# endoreg_db/views/media_management.py

import logging
from datetime import timedelta
from typing import Any, Dict

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from numpy import delete
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.views import APIView

from endoreg_db.models import RawPdfFile, VideoFile, VideoState
from endoreg_db.utils.permissions import DEBUG_PERMISSIONS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Media Cleanup and Management API
# ---------------------------------------------------------------------------


class MediaManagementView(APIView):
    """
    Comprehensive Media Management API for cleanup and maintenance operations
    """

    permission_classes = DEBUG_PERMISSIONS

    def get(self, request):
        """
        GET /api/media-management/status/
        Get overview of media status and cleanup opportunities
        """
        try:
            status_overview = self._get_status_overview()
            return Response(status_overview)
        except Exception as e:
            logger.error(f"Error getting media status overview: {e}")
            return Response(
                {"error": "Failed to get status overview"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def delete(self, request):
        """
        DELETE /api/media-management/cleanup/
        Cleanup unfinished, failed, or stale media processing entries
        """
        cleanup_type = request.query_params.get("type", "unfinished")
        force = request.query_params.get("force", "false").lower() == "true"
        media_type = request.query_params.get("file_type", "all")
        file_id = request.query_params.get("file_id", None)

        try:
            result = self._perform_cleanup(cleanup_type, force, media_type, file_id)

            return Response(result)
        except Exception as e:
            logger.error(f"Error during media cleanup: {e}")
            return Response(
                {"error": "Cleanup operation failed"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _get_status_overview(self) -> Dict[str, Any]:
        """Get comprehensive status overview"""
        video_stats = self._get_video_stats()
        pdf_stats = self._get_pdf_stats()

        # Stale processing detection (older than 2 hours)
        stale_threshold = timezone.now() - timedelta(hours=2)
        # Use VideoState boolean fields instead of non-existent name field
        stale_videos = VideoFile.objects.filter(
            uploaded_at__lt=stale_threshold,
            state__frames_extracted=True,
            state__sensitive_meta_processed=False,
        ).count()

        return {
            "videos": video_stats,
            "pdfs": pdf_stats,
            "cleanup_opportunities": {
                "stale_processing": stale_videos,
                "failed_videos": video_stats["failed"],
                "unfinished_total": video_stats["unfinished"] + pdf_stats["unfinished"],
            },
            "total_files": video_stats["total"] + pdf_stats["total"],
            "timestamp": timezone.now().isoformat(),
        }

    def _get_video_stats(self) -> Dict[str, int]:
        """
        Compute counts of VideoFile records grouped by their anonymization/processing status.
        
        Returns:
            stats (Dict[str, int]): Mapping with the following keys:
                - "total": total number of VideoFile records.
                - "not_started": count of videos with no state or status "not_started".
                - "processing": count of videos in "extracting_frames" or "processing_anonymization".
                - "done": count of videos with status "done_processing_anonymization".
                - "failed": count of videos with status "failed".
                - "validated": count of videos with status "validated".
                - "unfinished": count of videos considered unfinished (includes not started, processing, and failed).
        """
        videos = VideoFile.objects.select_related("state").all()

        stats = {
            "total": videos.count(),
            "not_started": 0,
            "processing": 0,
            "done": 0,
            "failed": 0,
            "validated": 0,
            "unfinished": 0,
        }

        for video in videos:
            if not video.state:
                stats["not_started"] += 1
                stats["unfinished"] += 1
                continue

            # Use the anonymization_status property
            video_status = video.state.anonymization_status

            if video_status.value == "not_started":
                stats["not_started"] += 1
                stats["unfinished"] += 1
            elif video_status.value in [
                "extracting_frames",
                "processing_anonymization",
            ]:
                stats["processing"] += 1
                stats["unfinished"] += 1
            elif video_status.value == "done_processing_anonymization":
                stats["done"] += 1
            elif video_status.value == "failed":
                stats["failed"] += 1
                stats["unfinished"] += 1
            elif video_status.value == "validated":
                stats["validated"] += 1
            else:
                stats["unfinished"] += 1
        return stats

    def _get_pdf_stats(self) -> Dict[str, int]:
        """
        Compute anonymization and validation statistics for all RawPdfFile records.
        
        Returns:
            stats (Dict[str, int]): A mapping of PDF status counters with the following keys:
                - total: total number of PDF records.
                - not_started: PDFs without anonymized text.
                - processing: PDFs currently in processing (reserved; always 0 in current implementation).
                - done: PDFs that have anonymized text but are not validated.
                - failed: PDFs marked as failed (reserved; always 0 in current implementation).
                - validated: PDFs that have anonymized text and are validated.
                - unfinished: PDFs considered unfinished (includes `not_started` and any other unfinished cases).
        """
        pdfs = RawPdfFile.objects.all()

        stats = {
            "total": pdfs.count(),
            "not_started": 0,
            "processing": 0,
            "done": 0,
            "failed": 0,
            "validated": 0,
            "unfinished": 0,
        }

        for pdf in pdfs:
            # PDF status logic based on anonymized_text presence and validation
            has_anonymized = bool(pdf.anonymized_text and pdf.anonymized_text.strip())
            is_validated = (
                getattr(pdf.sensitive_meta, "is_verified", False)
                if pdf.sensitive_meta
                else False
            )

            if not has_anonymized:
                stats["not_started"] += 1
                stats["unfinished"] += 1
            elif has_anonymized and is_validated:
                stats["validated"] += 1
            elif has_anonymized and not is_validated:
                stats["done"] += 1
            else:
                stats["unfinished"] += 1

        return stats

    def _perform_cleanup(
        self, cleanup_type: str, force: bool, media_type: str, file_id: int
    ) -> Dict[str, Any]:
        """
        Perform the requested cleanup operation for video and/or PDF media.
        
        Parameters:
            cleanup_type (str): Cleanup mode to run. Allowed values: "unfinished", "failed", "stale", "all".
            force (bool): If True, perform deletions; if False, perform a dry run (no deletions).
            media_type (str): Target media type. Allowed values: "video", "pdf", "all".
            file_id (int): Optional ID of a specific media item to remove prior to running the cleanup. If provided, will be cast to int.
        
        Returns:
            result (dict): Summary of the cleanup action with keys:
                - "cleanup_type": the requested cleanup_type.
                - "force": the provided force flag.
                - "removed_items": list of items identified (and deleted when force is True); each item is a dict with details about the media removed or targeted.
                - "summary": operation-specific summary data; for "all" this is a mapping with "unfinished", "failed", and "stale" sub-summaries.
        
        Raises:
            ValueError: If media_type or cleanup_type is unknown, or if file_id cannot be converted to int.
        """

        result = {
            "cleanup_type": cleanup_type,
            "force": force,
            "removed_items": [],
            "summary": {},
        }

        if media_type not in ["video", "pdf", "all"]:
            raise ValueError(f"Unknown media type: {media_type}")

        if file_id is not None:
            try:
                file_id = int(file_id)
            except ValueError:
                raise ValueError(f"Invalid file ID: {file_id}")

        video_file_obj = None
        pdf_file_obj = None

        if media_type == "video":
            video_file_obj = (
                VideoFile.get_video_by_id(self, video_id=file_id) if file_id else None
            )
        elif media_type == "pdf":
            pdf_file_obj = RawPdfFile.get_pdf_by_id(file_id) if file_id else None

        with transaction.atomic():
            if video_file_obj:
                video_file_obj.delete()
            if pdf_file_obj:
                pdf_file_obj.delete()

            if cleanup_type == "unfinished":
                result.update(self._cleanup_unfinished_media(force))
            elif cleanup_type == "failed":
                result.update(self._cleanup_failed_media(force))
            elif cleanup_type == "stale":
                result.update(self._cleanup_stale_processing(force))
            elif cleanup_type == "all":
                unfinished = self._cleanup_unfinished_media(force)
                failed = self._cleanup_failed_media(force)
                stale = self._cleanup_stale_processing(force)

                result["removed_items"] = (
                    unfinished.get("removed_items", [])
                    + failed.get("removed_items", [])
                    + stale.get("removed_items", [])
                )
                result["summary"] = {
                    "unfinished": unfinished.get("summary", {}),
                    "failed": failed.get("summary", {}),
                    "stale": stale.get("summary", {}),
                }
            else:
                raise ValueError(f"Unknown cleanup type: {cleanup_type}")

        return result

    def _cleanup_unfinished_media(self, force: bool) -> Dict[str, Any]:
        """Remove unfinished media processing entries"""
        removed_videos = []
        removed_pdfs = []

        # Find unfinished videos using VideoState boolean fields
        unfinished_videos = VideoFile.objects.select_related("state").all()

        for video in unfinished_videos:
            if not video.state:
                if force:  # Only remove videos without state if force=True
                    removed_videos.append(
                        {
                            "id": video.id,
                            "type": "video",
                            "filename": video.original_file_name,
                            "status": "no_state",
                            "uploaded_at": video.uploaded_at.isoformat(),
                        }
                    )
                    video.delete()
                continue

            video_status = video.state.anonymization_status
            is_unfinished = video_status.value in [
                "not_started",
                "extracting_frames",
                "processing_anonymization",
                "failed",
            ]

            # Remove unfinished videos
            if is_unfinished and (force or video_status.value != "not_started"):
                removed_videos.append(
                    {
                        "id": video.id,
                        "type": "video",
                        "filename": video.original_file_name,
                        "status": video_status.value,
                        "uploaded_at": video.uploaded_at.isoformat(),
                    }
                )
                if force:
                    video.delete()

        # Return the results
        return {
            "removed_items": removed_videos + removed_pdfs,
            "summary": {
                "videos_removed": len(removed_videos),
                "pdfs_removed": len(removed_pdfs),
                "total_removed": len(removed_videos) + len(removed_pdfs),
                "dry_run": not force,
            },
        }

    def _cleanup_failed_media(self, force: bool) -> Dict[str, Any]:
        """Remove failed media processing entries"""
        removed_items = []

        # Find failed videos using VideoState boolean fields
        failed_videos = VideoFile.objects.select_related("state").all()

        for video in failed_videos:
            if video.state and video.state.anonymization_status.value == "failed":
                removed_items.append(
                    {
                        "id": video.id,
                        "type": "video",
                        "filename": video.original_file_name,
                        "status": "failed",
                        "uploaded_at": video.uploaded_at.isoformat(),
                    }
                )
                if force:
                    video.delete()

        if force:
            # Count actual deletions
            videos_deleted = len([v for v in removed_items if v["type"] == "video"])
            return {
                "removed_items": removed_items,
                "summary": {
                    "videos_removed": videos_deleted,
                    "total_removed": videos_deleted,
                    "dry_run": False,
                },
            }
        else:
            return {
                "removed_items": removed_items,
                "summary": {
                    "videos_removed": len(removed_items),
                    "total_removed": len(removed_items),
                    "dry_run": True,
                },
            }

    def _cleanup_stale_processing(self, force: bool) -> Dict[str, Any]:
        """Remove stale processing entries (older than 2 hours)"""
        stale_threshold = timezone.now() - timedelta(hours=2)
        removed_items = []

        # Find stale videos using VideoState boolean fields
        stale_videos = VideoFile.objects.filter(
            uploaded_at__lt=stale_threshold,
            state__frames_extracted=True,
            state__sensitive_meta_processed=False,
        ).select_related("state")

        for video in stale_videos:
            video_status = (
                video.state.anonymization_status if video.state else "no_state"
            )
            removed_items.append(
                {
                    "id": video.id,
                    "type": "video",
                    "filename": video.original_file_name,
                    "status": f"stale_{video_status.value if hasattr(video_status, 'value') else video_status}",
                    "uploaded_at": video.uploaded_at.isoformat(),
                    "stale_duration_hours": (
                        timezone.now() - video.uploaded_at
                    ).total_seconds()
                    / 3600,
                }
            )

        if force:
            videos_deleted = stale_videos.delete()[0]
            return {
                "removed_items": removed_items,
                "summary": {
                    "stale_videos_removed": videos_deleted,
                    "total_removed": videos_deleted,
                    "dry_run": False,
                },
            }
        else:
            return {
                "removed_items": removed_items,
                "summary": {
                    "stale_videos_removed": len(removed_items),
                    "total_removed": len(removed_items),
                    "dry_run": True,
                },
            }


@api_view(["DELETE"])
@permission_classes(DEBUG_PERMISSIONS)
def force_remove_media(request, file_id: int):
    """
    Force-remove a media item (video or PDF) by its ID.
    
    Attempts to delete a VideoFile with the given ID first; if not found, attempts to delete a RawPdfFile. On successful deletion returns a response containing a human-readable detail message, `file_type` ("video" or "pdf"), and `file_id`. If no matching media is found returns a 404 response with `{"detail": "File not found"}`. On unexpected errors returns a 500 response with `{"error": "Force removal failed"}`.
    
    Parameters:
        request: The HTTP request object.
        file_id (int): Identifier of the media item to remove.
    
    Returns:
        Response: On success, a JSON response with keys `detail`, `file_type`, and `file_id`; on not found a 404 JSON response; on error a 500 JSON response.
    """
    try:
        # Try to find and delete from VideoFile first
        try:
            video = VideoFile.objects.get(id=file_id)
            filename = video.original_file_name
            video.delete()

            return Response(
                {
                    "detail": f"Video file '{filename}' (ID: {file_id}) removed successfully",
                    "file_type": "video",
                    "file_id": file_id,
                }
            )
        except VideoFile.DoesNotExist:
            pass

        # Try to find and delete from RawPdfFile
        try:
            pdf = RawPdfFile.objects.get(id=file_id)
            filename = getattr(pdf.file, "name", "Unknown")
            pdf.delete()

            return Response(
                {
                    "detail": f"PDF file '{filename}' (ID: {file_id}) removed successfully",
                    "file_type": "pdf",
                    "file_id": file_id,
                }
            )
        except RawPdfFile.DoesNotExist:
            pass

        return Response({"detail": "File not found"}, status=status.HTTP_404_NOT_FOUND)

    except Exception as e:
        logger.error(f"Error force removing media {file_id}: {e}")
        return Response(
            {"error": "Force removal failed"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["POST"])
@permission_classes(DEBUG_PERMISSIONS)
def reset_processing_status(request, file_id: int):
    """
    Reset the processing status of a media item identified by `file_id`.
    
    Attempts to reset a VideoFile to the "not_started" VideoState; if no VideoFile with the given id exists, clears the `anonymized_text` of a RawPdfFile with that id. Returns a 404 response when no media item is found and a 500 response on unexpected errors.
    
    Parameters:
        request: The HTTP request object.
        file_id (int): ID of the media file to reset.
    
    Returns:
        rest_framework.response.Response: On success for a video: a response containing `detail`, `file_type: "video"`, `file_id`, and `new_status: "not_started"`. On success for a PDF: a response containing `detail`, `file_type: "pdf"`, `file_id`, and `new_status: "not_started"`. If no file is found: a 404 response with `{"detail": "File not found"}`. On unexpected failure: a 500 response with `{"error": "Status reset failed"}`.
    """
    try:
        # Try VideoFile first
        try:
            video = VideoFile.objects.get(id=file_id)

            # Reset to 'not_started' state
            not_started_state, created = VideoState.objects.get_or_create(
                name="not_started"
            )
            video.state = not_started_state
            video.save()

            return Response(
                {
                    "detail": "Video file status reset to 'not_started'",
                    "file_type": "video",
                    "file_id": file_id,
                    "new_status": "not_started",
                }
            )
        except VideoFile.DoesNotExist:
            pass

        # PDF files don't have state, but we can clear anonymized_text
        try:
            pdf = RawPdfFile.objects.get(id=file_id)
            pdf.anonymized_text = ""
            pdf.save()

            return Response(
                {
                    "detail": "PDF file processing reset",
                    "file_type": "pdf",
                    "file_id": file_id,
                    "new_status": "not_started",
                }
            )
        except RawPdfFile.DoesNotExist:
            pass

        return Response({"detail": "File not found"}, status=status.HTTP_404_NOT_FOUND)

    except Exception as e:
        logger.error(f"Error resetting status for media {file_id}: {e}")
        return Response(
            {"error": "Status reset failed"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
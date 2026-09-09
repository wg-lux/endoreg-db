from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Protocol, cast

from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.raw_pdf_files import get_raw_pdf_by_pk
from endoreg_db.services.video_files import get_video_by_pk
from endoreg_db.utils.permissions import DEBUG_PERMISSIONS
from lx_dtypes.models.contracts.media_management import (
    MediaManagementCleanupQueryPayload,
    MediaManagementCleanupResultPayload,
    MediaManagementForceRemoveResponsePayload,
    MediaManagementItemPayload,
    MediaManagementResetStatusResponsePayload,
    MediaManagementSummaryPayload,
)

logger = logging.getLogger(__name__)


class _VideoRecord(Protocol):
    id: int
    original_file_name: str | None
    uploaded_at: datetime
    video_hash: str

    def delete(self) -> None: ...


class _PdfRecord(Protocol):
    id: int
    file: object
    pdf_hash: str

    def delete(self) -> None: ...


class MediaManagementView(APIView):
    permission_classes = DEBUG_PERMISSIONS

    def get(self, request: Request) -> Response:
        try:
            return Response(self._get_status_overview())
        except Exception as exc:  # pragma: no cover - defensive boundary
            logger.error("Error getting media status overview: %s", exc)
            return Response(
                {"error": "Failed to get status overview"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def delete(self, request: Request) -> Response:
        query = MediaManagementCleanupQueryPayload.model_validate(
            {
                "cleanup_type": request.query_params.get("type", "unfinished"),
                "force": str(request.query_params.get("force", "false")).lower()
                == "true",
                "media_type": request.query_params.get("file_type", "all"),
                "file_id": request.query_params.get("file_id"),
            }
        )
        try:
            result = self._perform_cleanup(query)
            return Response(result.model_dump(mode="python"))
        except Exception as exc:  # pragma: no cover - defensive boundary
            logger.error("Error during media cleanup: %s", exc)
            return Response(
                {"error": "Cleanup operation failed"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _get_status_overview(self) -> dict[str, object]:
        video_stats = self._get_video_stats()
        pdf_stats = self._get_pdf_stats()
        stale_threshold = timezone.now() - timedelta(hours=2)
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

    def _get_video_stats(self) -> dict[str, int]:
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
            video_status = video.state.anonymization_status.value
            if video_status == "not_started":
                stats["not_started"] += 1
                stats["unfinished"] += 1
            elif video_status in {"extracting_frames", "processing_anonymization"}:
                stats["processing"] += 1
                stats["unfinished"] += 1
            elif video_status == "done_processing_anonymization":
                stats["done"] += 1
            elif video_status == "failed":
                stats["failed"] += 1
                stats["unfinished"] += 1
            elif video_status == "validated":
                stats["validated"] += 1
            else:
                stats["unfinished"] += 1
        return stats

    def _get_pdf_stats(self) -> dict[str, int]:
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
            has_anonymized = bool(pdf.anonymized_text and pdf.anonymized_text.strip())
            is_validated = bool(
                getattr(pdf.sensitive_meta, "is_verified", False)
                if pdf.sensitive_meta
                else False
            )
            if not has_anonymized:
                stats["not_started"] += 1
                stats["unfinished"] += 1
            elif is_validated:
                stats["validated"] += 1
            else:
                stats["done"] += 1
        return stats

    def _perform_cleanup(
        self, query: MediaManagementCleanupQueryPayload
    ) -> MediaManagementCleanupResultPayload:
        video_file_obj = None
        pdf_file_obj = None
        if query.media_type == "video":
            video_file_obj = (
                get_video_by_pk(pk=query.file_id) if query.file_id else None
            )
        elif query.media_type == "pdf":
            pdf_file_obj = (
                get_raw_pdf_by_pk(pk=query.file_id) if query.file_id else None
            )

        with transaction.atomic():
            if video_file_obj:
                video_file_obj.delete()
            if pdf_file_obj:
                pdf_file_obj.delete()

            if query.cleanup_type == "unfinished":
                return self._cleanup_unfinished_media(query.force)
            if query.cleanup_type == "failed":
                return self._cleanup_failed_media(query.force)
            if query.cleanup_type == "stale":
                return self._cleanup_stale_processing(query.force)

            unfinished = self._cleanup_unfinished_media(query.force)
            failed = self._cleanup_failed_media(query.force)
            stale = self._cleanup_stale_processing(query.force)
            merged_items = (
                unfinished.removed_items + failed.removed_items + stale.removed_items
            )
            return MediaManagementCleanupResultPayload(
                cleanup_type=query.cleanup_type,
                force=query.force,
                removed_items=merged_items,
                summary=MediaManagementSummaryPayload(
                    videos_removed=unfinished.summary.videos_removed
                    + failed.summary.videos_removed,
                    pdfs_removed=unfinished.summary.pdfs_removed
                    + failed.summary.pdfs_removed,
                    total_removed=(
                        unfinished.summary.total_removed
                        + failed.summary.total_removed
                        + stale.summary.total_removed
                    ),
                    stale_videos_removed=stale.summary.stale_videos_removed,
                    dry_run=not query.force,
                ),
            )

    def _cleanup_unfinished_media(
        self, force: bool
    ) -> MediaManagementCleanupResultPayload:
        removed_items: list[MediaManagementItemPayload] = []
        unfinished_videos = VideoFile.objects.select_related("state").all()
        for video in unfinished_videos:
            if not video.state:
                if force:
                    video_record = cast(_VideoRecord, video)
                    removed_items.append(
                        MediaManagementItemPayload(
                            id=video_record.id,
                            type="video",
                            filename=video_record.original_file_name,
                            status="no_state",
                            uploaded_at=video_record.uploaded_at.isoformat(),
                        )
                    )
                    video_record.delete()
                continue
            video_status = video.state.anonymization_status.value
            is_unfinished = video_status in {
                "not_started",
                "extracting_frames",
                "processing_anonymization",
                "failed",
            }
            if is_unfinished and (force or video_status != "not_started"):
                video_record = cast(_VideoRecord, video)
                removed_items.append(
                    MediaManagementItemPayload(
                        id=video_record.id,
                        type="video",
                        filename=video_record.original_file_name,
                        status=video_status,
                        uploaded_at=video_record.uploaded_at.isoformat(),
                    )
                )
                if force:
                    video_record.delete()
        return MediaManagementCleanupResultPayload(
            cleanup_type="unfinished",
            force=force,
            removed_items=removed_items,
            summary=MediaManagementSummaryPayload(
                videos_removed=len(removed_items),
                pdfs_removed=0,
                total_removed=len(removed_items),
                dry_run=not force,
            ),
        )

    def _cleanup_failed_media(self, force: bool) -> MediaManagementCleanupResultPayload:
        removed_items: list[MediaManagementItemPayload] = []
        failed_videos = VideoFile.objects.select_related("state").all()
        for video in failed_videos:
            if video.state and video.state.anonymization_status.value == "failed":
                video_record = cast(_VideoRecord, video)
                removed_items.append(
                    MediaManagementItemPayload(
                        id=video_record.id,
                        type="video",
                        filename=video_record.original_file_name,
                        status="failed",
                        uploaded_at=video_record.uploaded_at.isoformat(),
                    )
                )
                if force:
                    video_record.delete()
        deleted_count = len(removed_items)
        return MediaManagementCleanupResultPayload(
            cleanup_type="failed",
            force=force,
            removed_items=removed_items,
            summary=MediaManagementSummaryPayload(
                videos_removed=deleted_count,
                pdfs_removed=0,
                total_removed=deleted_count,
                dry_run=not force,
            ),
        )

    def _cleanup_stale_processing(
        self, force: bool
    ) -> MediaManagementCleanupResultPayload:
        stale_threshold = timezone.now() - timedelta(hours=2)
        removed_items: list[MediaManagementItemPayload] = []
        stale_videos = VideoFile.objects.filter(
            uploaded_at__lt=stale_threshold,
            state__frames_extracted=True,
            state__sensitive_meta_processed=False,
        ).select_related("state")
        for video in stale_videos:
            video_record = cast(_VideoRecord, video)
            video_status = video.state.anonymization_status if video.state else None
            status_value = (
                f"stale_{video_status.value}"
                if video_status is not None
                else "stale_no_state"
            )
            removed_items.append(
                MediaManagementItemPayload(
                    id=video_record.id,
                    type="video",
                    filename=video_record.original_file_name,
                    status=status_value,
                    uploaded_at=video_record.uploaded_at.isoformat(),
                    stale_duration_hours=(
                        timezone.now() - video_record.uploaded_at
                    ).total_seconds()
                    / 3600,
                )
            )
        deleted_count = stale_videos.delete()[0] if force else len(removed_items)
        return MediaManagementCleanupResultPayload(
            cleanup_type="stale",
            force=force,
            removed_items=removed_items,
            summary=MediaManagementSummaryPayload(
                videos_removed=0,
                pdfs_removed=0,
                total_removed=deleted_count,
                stale_videos_removed=deleted_count,
                dry_run=not force,
            ),
        )


@api_view(["DELETE"])
@permission_classes(DEBUG_PERMISSIONS)
def force_remove_media(request: Request, file_id: int) -> Response:
    try:
        try:
            video = cast(_VideoRecord, VideoFile.objects.get(id=file_id))
            filename = video.original_file_name
            video.delete()
            job = UploadJob.objects.get(content_hash=video.video_hash)
            job.delete()
            payload = MediaManagementForceRemoveResponsePayload(
                detail=f"Video file '{filename}' (ID: {file_id}) removed successfully",
                file_type="video",
                file_id=file_id,
            )
            return Response(payload.model_dump(mode="python"))
        except VideoFile.DoesNotExist:
            pass

        try:
            pdf = cast(_PdfRecord, RawPdfFile.objects.get(id=file_id))
            filename = getattr(pdf.file, "name", "Unknown")
            pdf.delete()
            job = UploadJob.objects.get(content_hash=pdf.pdf_hash)
            job.delete()
            payload = MediaManagementForceRemoveResponsePayload(
                detail=f"report file '{filename}' (ID: {file_id}) removed successfully",
                file_type="pdf",
                file_id=file_id,
            )
            return Response(payload.model_dump(mode="python"))
        except RawPdfFile.DoesNotExist:
            pass

        return Response(
            {"detail": "File not found"},
            status=status.HTTP_404_NOT_FOUND,
        )
    except Exception as exc:  # pragma: no cover - defensive boundary
        logger.error("Error force removing media %s: %s", file_id, exc)
        return Response(
            {"error": "Force removal failed"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["POST"])
@permission_classes(DEBUG_PERMISSIONS)
def reset_processing_status(request: HttpRequest, file_id: int) -> Response:
    try:
        try:
            video = VideoFile.objects.get(id=file_id)
            if video.state is not None:
                state = video.state
                setattr(state, "processing_finished", False)
                setattr(state, "anonymization_status_id", None)
                state.save()
            payload = MediaManagementResetStatusResponsePayload(
                detail="Video processing status reset",
                file_type="video",
                file_id=file_id,
            ).model_dump(mode="python")
            payload["new_status"] = "not_started"
            return Response(payload)
        except VideoFile.DoesNotExist:
            pass

        try:
            pdf = RawPdfFile.objects.get(id=file_id)
            if pdf.sensitive_meta and pdf.sensitive_meta.state:
                state = pdf.sensitive_meta.state
                setattr(state, "processing_finished", False)
                state.save()
                payload = MediaManagementResetStatusResponsePayload(
                    detail="Report processing status reset",
                    file_type="pdf",
                    file_id=file_id,
                )
                return Response(payload.model_dump(mode="python"))
        except RawPdfFile.DoesNotExist:
            pass

        return Response(
            {"detail": "File not found"},
            status=status.HTTP_404_NOT_FOUND,
        )
    except Exception as exc:  # pragma: no cover - defensive boundary
        logger.error("Error resetting status for media %s: %s", file_id, exc)
        return Response(
            {"error": "Reset status failed"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

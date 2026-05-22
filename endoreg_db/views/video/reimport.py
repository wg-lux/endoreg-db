import logging
from typing import Any

from django.db import transaction
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from endoreg_db.services.jobs.video_reimport_jobs import (
    _as_bool,
    _dispatch_prediction_refresh,
    _mark_upload_jobs_anonymized,
    _mark_upload_jobs_error,
    _mark_upload_jobs_lost,
    _reset_reimport_state,
    _video_has_integrity_loss,
)
from endoreg_db.services.video_temporal_inference import (
    TemporalInferenceConfigError,
)
from endoreg_db.utils.storage import ensure_local_file

from ...models import AiModel, ModelMeta, VideoFile
from ...models import SensitiveMeta as SensitiveMeta
from ...services.video_import import VideoImportService

logger = logging.getLogger(__name__)


class VideoReimportView(APIView):
    """
    API endpoint to re-import a video file and regenerate metadata.
    This is useful when OCR failed or metadata is incomplete.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.video_service = VideoImportService()

    def post(self, request, pk):
        """
        Re-import a video file to regenerate SensitiveMeta and other metadata.
        Instead of creating a new video, this updates the existing one.

        Args:
            pk (int): Primary key of the VideoFile to reimport
        """
        if not pk or not isinstance(pk, int):
            return Response(
                {"error": "Invalid video ID provided."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            video = VideoFile.objects.get(id=pk)
            logger.info("Found video %s (ID: %s) for re-import", video.video_hash, pk)
        except VideoFile.DoesNotExist:
            logger.warning("Video with ID %s not found", pk)
            return Response(
                {"error": f"Video with ID {pk} not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if _video_has_integrity_loss(video):
            return Response(
                {
                    "error": "Video is marked failed/lost by media integrity.",
                    "error_type": "integrity_lost",
                    "video_id": pk,
                    "uuid": str(video.video_hash),
                },
                status=status.HTTP_409_CONFLICT,
            )

        if not video.raw_file:
            logger.warning("Video %s has no raw file", video.video_hash)
            return Response(
                {
                    "error": (
                        "Raw video source is missing for this video. "
                        "Please upload the original raw video again before re-importing."
                    ),
                    "error_type": "missing_source",
                    "video_id": pk,
                    "uuid": str(video.video_hash),
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        if not video.center:
            logger.warning("Video %s has no associated center", video.video_hash)
            return Response(
                {"error": "Video has no associated center."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        request_data = getattr(request, "data", {})
        payload = request_data if hasattr(request_data, "get") else {}

        try:
            from endoreg_db.services.jobs.video_reimport_jobs import (
                dispatch_video_reimport,
                get_video_reimport_job_mode,
            )
        except Exception as exc:
            logger.exception("Video re-import dispatcher could not be loaded.")
            return Response(
                {
                    "status": "failed",
                    "operation": "video_reimport",
                    "reason": str(exc),
                    "error": "Video re-import dispatch failed.",
                    "error_type": "dispatch_error",
                    "video_id": pk,
                    "uuid": str(video.video_hash),
                    "updated_in_place": True,
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        if get_video_reimport_job_mode() == "inline":
            return self._run_inline_video_reimport(
                video=video,
                pk=pk,
                payload=payload,
            )

        try:
            dispatch_result = dispatch_video_reimport(video_id=pk, payload=payload)
        except Exception as exc:
            logger.exception(
                "Video re-import dispatch failed for %s.", video.video_hash
            )
            return Response(
                {
                    "status": "failed",
                    "operation": "video_reimport",
                    "reason": str(exc),
                    "error": "Video re-import dispatch failed.",
                    "error_type": "dispatch_error",
                    "video_id": pk,
                    "uuid": str(video.video_hash),
                    "updated_in_place": True,
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        response_payload = {
            **dispatch_result.to_dict(),
            "video_id": pk,
            "uuid": str(video.video_hash),
            "updated_in_place": True,
        }

        if dispatch_result.status == "busy":
            return Response(
                {
                    **response_payload,
                    "error": "Video media is currently busy.",
                    "error_type": "media_busy",
                },
                status=status.HTTP_409_CONFLICT,
            )

        if dispatch_result.status == "lost":
            return Response(
                {
                    **response_payload,
                    "error": (
                        "Raw video source could not be materialized from storage."
                    ),
                    "error_type": "missing_source",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        if dispatch_result.status == "failed":
            inline_failure = dispatch_result.mode == "inline"
            return Response(
                {
                    **response_payload,
                    "error": (
                        "Video re-import failed."
                        if inline_failure
                        else "Video re-import dispatch failed."
                    ),
                    "error_type": (
                        "processing_error" if inline_failure else "dispatch_error"
                    ),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        if dispatch_result.status == "completed":
            return Response(
                {
                    **response_payload,
                    "message": "Video re-import completed.",
                },
                status=status.HTTP_200_OK,
            )

        return Response(
            {
                **response_payload,
                "message": (
                    "Video re-import is already queued."
                    if dispatch_result.status == "already_queued"
                    else "Video re-import queued."
                ),
            },
            status=status.HTTP_202_ACCEPTED,
        )

    def _run_inline_video_reimport(
        self,
        *,
        video: VideoFile,
        pk: int,
        payload: dict[str, Any],
    ) -> Response:
        try:
            logger.info(
                "Starting in-place re-import for video %s (ID: %s)",
                video.video_hash,
                pk,
            )
            try:
                reset_upload_jobs = self._run_video_import_service(video)
            except FileNotFoundError as exc:
                error_detail = (
                    f"Raw video source could not be materialized from storage. {exc}"
                )
                logger.warning(
                    "Raw source missing during video re-import for %s: %s",
                    video.video_hash,
                    exc,
                )
                _mark_upload_jobs_lost(video, error_detail)
                return Response(
                    {
                        "error": (
                            "Raw video source could not be materialized from storage. "
                            "Please upload the original raw video again before re-importing."
                        ),
                        "error_type": "missing_source",
                        "video_id": pk,
                        "uuid": str(video.video_hash),
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )
            except Exception as exc:
                logger.exception(
                    "VideoImportService reprocessing failed for video %s: %s",
                    video.video_hash,
                    exc,
                )
                _mark_upload_jobs_error(video, str(exc))
                return Response(
                    {
                        "error": f"Video re-import processing failed: {str(exc)}",
                        "error_type": "processing_error",
                        "video_id": pk,
                        "uuid": str(video.video_hash),
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            video.refresh_from_db()
            completed_upload_jobs = _mark_upload_jobs_anonymized(video)
            prediction_refresh = self._maybe_dispatch_prediction_refresh(
                video=video,
                payload=payload,
            )

            logger.info(
                "Video re-import completed successfully for %s",
                video.video_hash,
            )
            return Response(
                {
                    "message": (
                        "Video re-import with VideoImportService completed "
                        "successfully."
                    ),
                    "video_id": pk,
                    "uuid": str(video.video_hash),
                    "frame_cleaning_applied": True,
                    "sensitive_meta_created": video.sensitive_meta is not None,
                    "sensitive_meta_id": (
                        video.sensitive_meta.id if video.sensitive_meta else None
                    ),
                    "reset_upload_jobs": reset_upload_jobs,
                    "completed_upload_jobs": completed_upload_jobs,
                    "prediction_refresh": prediction_refresh,
                    "updated_in_place": True,
                    "status": "done",
                },
                status=status.HTTP_200_OK,
            )
        except Exception as exc:
            logger.error(
                "Failed to re-import video %s: %s",
                video.video_hash,
                exc,
                exc_info=True,
            )

            error_msg = str(exc)
            if any(
                phrase in error_msg.lower()
                for phrase in ["insufficient storage", "no space left", "disk full"]
            ):
                return Response(
                    {
                        "error": f"Storage error during re-import: {error_msg}",
                        "error_type": "storage_error",
                        "video_id": pk,
                        "uuid": str(video.video_hash),
                    },
                    status=status.HTTP_507_INSUFFICIENT_STORAGE,
                )

            return Response(
                {
                    "error": f"Re-import failed: {error_msg}",
                    "error_type": "processing_error",
                    "video_id": pk,
                    "uuid": str(video.video_hash),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _run_video_import_service(self, video: VideoFile) -> int:
        with ensure_local_file(video.raw_file) as raw_file_path:
            with transaction.atomic():
                reset_upload_jobs = _reset_reimport_state(video)

            processor_name = (
                video.video_meta.processor.name
                if video.video_meta and video.video_meta.processor
                else "Unknown"
            )
            logger.info(
                "Starting VideoImportService reprocessing for %s",
                video.video_hash,
            )
            self.video_service.import_and_anonymize(
                file_path=raw_file_path,
                center_name=video.center.name,
                processor_name=processor_name,
                retry=True,
            )
        return reset_upload_jobs

    def _maybe_dispatch_prediction_refresh(
        self,
        *,
        video: VideoFile,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if not _as_bool(payload.get("refresh_predictions"), default=True):
            return {
                "status": "skipped",
                "queued": False,
                "reason": "disabled",
            }

        try:
            return _dispatch_prediction_refresh(video, payload)
        except (
            AiModel.DoesNotExist,
            ModelMeta.DoesNotExist,
            TemporalInferenceConfigError,
            ValueError,
        ) as exc:
            logger.warning(
                "Video re-import completed but prediction refresh was not queued "
                "for video %s: %s",
                video.video_hash,
                exc,
            )
            return {
                "status": "not_queued",
                "queued": False,
                "error": str(exc),
            }
        except Exception as exc:
            logger.exception(
                "Video re-import completed but prediction refresh dispatch failed "
                "for video %s.",
                video.video_hash,
            )
            return {
                "status": "failed",
                "queued": False,
                "error": str(exc),
            }

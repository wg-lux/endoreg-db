import logging
from typing import Any

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from ...models import AiModel, ModelMeta, SensitiveMeta, UploadJob, VideoFile
from ...services.video_import import VideoImportService
from endoreg_db.services.video_temporal_inference import (
    TemporalInferenceConfigError,
    dispatch_video_temporal_inference,
    extract_temporal_options,
)
from endoreg_db.utils.storage import ensure_local_file

logger = logging.getLogger(__name__)

DEFAULT_SEGMENTATION_MODEL_NAME = "image_multilabel_classification_colonoscopy_default"


def _as_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _resolve_prediction_model_meta(payload: dict[str, Any]) -> ModelMeta:
    model_meta_id = payload.get("model_meta_id")
    if model_meta_id not in (None, ""):
        return ModelMeta.objects.select_related("model", "labelset").get(
            pk=int(str(model_meta_id))
        )

    model_name = str(
        payload.get("model_name") or DEFAULT_SEGMENTATION_MODEL_NAME
    ).strip()
    model_meta_version = payload.get("model_meta_version")
    ai_model = AiModel.objects.get(name=model_name)
    if model_meta_version not in (None, ""):
        return ai_model.metadata_versions.select_related("model", "labelset").get(
            version=str(model_meta_version)
        )
    return ai_model.get_latest_version()


def _reset_reimport_state(video: VideoFile) -> int:
    old_meta_id = video.sensitive_meta_id
    if old_meta_id is not None:
        logger.info(
            "Clearing existing SensitiveMeta %s for video %s",
            old_meta_id,
            video.video_hash,
        )
        video.sensitive_meta = None
        video.save(update_fields=["sensitive_meta"])
        try:
            SensitiveMeta.objects.filter(id=old_meta_id).delete()
            logger.info("Deleted old SensitiveMeta %s", old_meta_id)
        except Exception as exc:
            logger.warning(
                "Could not delete old SensitiveMeta %s: %s",
                old_meta_id,
                exc,
            )

    reset_count = UploadJob.objects.filter(content_hash=video.video_hash).update(
        status=UploadJob.Status.PROCESSING,
        error_detail="",
        updated_at=timezone.now(),
    )
    logger.info(
        "Reset %d UploadJob row(s) to processing for video %s",
        reset_count,
        video.video_hash,
    )

    logger.info("Re-initializing video specs for %s", video.video_hash)
    video.initialize_video_specs()
    video.initialize_frames()
    return reset_count


def _mark_upload_jobs_anonymized(video: VideoFile) -> int:
    return UploadJob.objects.filter(content_hash=video.video_hash).update(
        status=UploadJob.Status.ANONYMIZED,
        error_detail="",
        sensitive_meta_id=video.sensitive_meta_id,
        updated_at=timezone.now(),
    )


def _mark_upload_jobs_error(video: VideoFile, error_detail: str) -> int:
    return UploadJob.objects.filter(content_hash=video.video_hash).update(
        status=UploadJob.Status.ERROR,
        error_detail=error_detail,
        updated_at=timezone.now(),
    )


def _video_has_integrity_loss(video: VideoFile) -> bool:
    get_state = getattr(video, "get_or_create_state", None)
    video_state = get_state() if callable(get_state) else getattr(video, "state", None)
    video_meta = getattr(video, "meta", None)
    if not isinstance(video_meta, dict):
        video_meta = {}
    return bool(
        getattr(video_state, "processing_error", False)
        or video_meta.get("integrity_status") == "lost"
    )


def _dispatch_prediction_refresh(
    video: VideoFile,
    payload: dict[str, Any],
) -> dict[str, Any]:
    model_meta = _resolve_prediction_model_meta(payload)
    test_run = _as_bool(payload.get("test_run"), default=False)
    try:
        n_test_frames = int(payload.get("n_test_frames") or 10)
    except (TypeError, ValueError) as exc:
        raise TemporalInferenceConfigError("n_test_frames must be an integer.") from exc

    dispatch_result = dispatch_video_temporal_inference(
        video_id=video.pk,
        model_meta_id=model_meta.pk,
        replace_prediction_segments=True,
        delete_frames_after=_as_bool(payload.get("delete_frames_after"), default=True),
        ocr_frame_fraction=0.001,
        ocr_cap=10,
        temporal_options=extract_temporal_options(payload),
        test_run=test_run,
        n_test_frames=n_test_frames,
    )
    payload = dispatch_result.to_dict()
    payload["queued"] = dispatch_result.status in {
        "queued",
        "already_queued",
        "completed",
    }
    return payload


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

        try:
            logger.info(
                "Starting in-place re-import for video %s (ID: %s)",
                video.video_hash,
                pk,
            )
            try:
                reset_upload_jobs = self._run_video_import_service(video)
            except FileNotFoundError as exc:
                logger.warning(
                    "Raw source missing during video re-import for %s: %s",
                    video.video_hash,
                    exc,
                )
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
            request_data = getattr(request, "data", {})
            payload = request_data if hasattr(request_data, "get") else {}
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

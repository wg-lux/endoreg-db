# pyright: reportPrivateUsage=false
from __future__ import annotations

import logging
from typing import Any, cast

from django.db import transaction
from django.db.models.fields.files import FieldFile
from lx_dtypes.models.contracts.video_reimport import (
    VideoReimportRequestData,
    video_reimport_json_safe_dict,
)
from rest_framework import status

from endoreg_db.models.administration.ai.ai_model import AiModel
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.metadata.model_meta import ModelMeta
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.services.jobs.video_reimport_jobs import (
    _as_bool,
    _dispatch_prediction_refresh,
    dispatch_video_reimport,
    get_video_reimport_job_mode,
    _mark_upload_jobs_anonymized,
    _mark_upload_jobs_error,
    _mark_upload_jobs_lost,
    _reset_reimport_state,
    _video_has_integrity_loss,
)
from endoreg_db.services.video_import import VideoImportService
from endoreg_db.services.video_temporal_inference import (
    TemporalInferenceConfigError,
)
from endoreg_db.utils.storage import ensure_local_file

logger = logging.getLogger(__name__)

VideoReimportResponse = tuple[dict[str, Any], int]


def _video_hash(video: VideoFile) -> str:
    return str(cast(object, getattr(video, "video_hash", "")))


def _video_raw_file(video: VideoFile) -> FieldFile | None:
    return cast(FieldFile | None, getattr(video, "raw_file", None))


def _video_center(video: VideoFile) -> object | None:
    return cast(object | None, getattr(video, "center", None))


def _video_sensitive_meta(video: VideoFile) -> SensitiveMeta | None:
    return cast(SensitiveMeta | None, getattr(video, "sensitive_meta", None))


def _video_sensitive_meta_id(video: VideoFile) -> int | None:
    sensitive_meta = _video_sensitive_meta(video)
    if sensitive_meta is None:
        return None
    return cast(
        int | None,
        getattr(sensitive_meta, "pk", None) or getattr(sensitive_meta, "id", None),
    )


class VideoReimportOrchestrator:
    def __init__(
        self,
        *,
        video: VideoFile,
        video_id: int,
        payload: VideoReimportRequestData,
        video_service: VideoImportService | None = None,
    ) -> None:
        self.video = video
        self.video_id = video_id
        self.payload = payload
        self.video_service = video_service or VideoImportService()
        self.video_hash = _video_hash(video)

    def run(self) -> VideoReimportResponse:
        if _video_has_integrity_loss(self.video):
            return (
                {
                    "error": "Video is marked failed/lost by media integrity.",
                    "error_type": "integrity_lost",
                    "video_id": self.video_id,
                    "uuid": self.video_hash,
                },
                status.HTTP_409_CONFLICT,
            )

        if not _video_raw_file(self.video):
            logger.warning("Video %s has no raw file", self.video_hash)
            return (
                {
                    "error": (
                        "Raw video source is missing for this video. "
                        "Please upload the original raw video again before re-importing."
                    ),
                    "error_type": "missing_source",
                    "video_id": self.video_id,
                    "uuid": self.video_hash,
                },
                status.HTTP_404_NOT_FOUND,
            )

        if _video_center(self.video) is None:
            logger.warning("Video %s has no associated center", self.video_hash)
            return (
                {"error": "Video has no associated center."},
                status.HTTP_400_BAD_REQUEST,
            )

        if get_video_reimport_job_mode() == "inline":
            return self._run_inline()
        return self._run_dispatched()

    def _run_dispatched(self) -> VideoReimportResponse:
        try:
            dispatch_result = dispatch_video_reimport(
                video_id=self.video_id,
                payload=self.payload,
            )
        except Exception as exc:
            logger.exception("Video re-import dispatch failed for %s.", self.video_hash)
            return (
                {
                    "status": "failed",
                    "operation": "video_reimport",
                    "reason": str(exc),
                    "error": "Video re-import dispatch failed.",
                    "error_type": "dispatch_error",
                    "video_id": self.video_id,
                    "uuid": self.video_hash,
                    "updated_in_place": True,
                },
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        response_payload: dict[str, Any] = {
            **dispatch_result.to_dict(),
            "video_id": self.video_id,
            "uuid": self.video_hash,
            "updated_in_place": True,
        }

        if dispatch_result.status == "busy":
            return (
                {
                    **response_payload,
                    "error": "Video media is currently busy.",
                    "error_type": "media_busy",
                },
                status.HTTP_409_CONFLICT,
            )

        if dispatch_result.status == "lost":
            return (
                {
                    **response_payload,
                    "error": (
                        "Raw video source could not be materialized from storage."
                    ),
                    "error_type": "missing_source",
                },
                status.HTTP_404_NOT_FOUND,
            )

        if dispatch_result.status == "failed":
            inline_failure = dispatch_result.mode == "inline"
            return (
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
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        if dispatch_result.status == "completed":
            return (
                {
                    **response_payload,
                    "message": "Video re-import completed.",
                },
                status.HTTP_200_OK,
            )

        return (
            {
                **response_payload,
                "message": (
                    "Video re-import is already queued."
                    if dispatch_result.status == "already_queued"
                    else "Video re-import queued."
                ),
            },
            status.HTTP_202_ACCEPTED,
        )

    def _run_inline(self) -> VideoReimportResponse:
        try:
            logger.info(
                "Starting in-place re-import for video %s (ID: %s)",
                self.video_hash,
                self.video_id,
            )
            try:
                reset_upload_jobs = self._run_video_import_service()
            except FileNotFoundError as exc:
                error_detail = (
                    f"Raw video source could not be materialized from storage. {exc}"
                )
                logger.warning(
                    "Raw source missing during video re-import for %s: %s",
                    self.video_hash,
                    exc,
                )
                _mark_upload_jobs_lost(self.video, error_detail)
                return (
                    {
                        "error": (
                            "Raw video source could not be materialized from storage. "
                            "Please upload the original raw video again before re-importing."
                        ),
                        "error_type": "missing_source",
                        "video_id": self.video_id,
                        "uuid": self.video_hash,
                    },
                    status.HTTP_404_NOT_FOUND,
                )
            except Exception as exc:
                logger.exception(
                    "VideoImportService reprocessing failed for video %s: %s",
                    self.video_hash,
                    exc,
                )
                _mark_upload_jobs_error(self.video, str(exc))
                return (
                    {
                        "error": f"Video re-import processing failed: {str(exc)}",
                        "error_type": "processing_error",
                        "video_id": self.video_id,
                        "uuid": self.video_hash,
                    },
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            self.video.refresh_from_db()
            completed_upload_jobs = _mark_upload_jobs_anonymized(self.video)
            prediction_refresh = self._maybe_dispatch_prediction_refresh()

            logger.info(
                "Video re-import completed successfully for %s",
                self.video_hash,
            )
            sensitive_meta = _video_sensitive_meta(self.video)
            return (
                {
                    "message": (
                        "Video re-import with VideoImportService completed "
                        "successfully."
                    ),
                    "video_id": self.video_id,
                    "uuid": self.video_hash,
                    "frame_cleaning_applied": True,
                    "sensitive_meta_created": sensitive_meta is not None,
                    "sensitive_meta_id": _video_sensitive_meta_id(self.video),
                    "reset_upload_jobs": reset_upload_jobs,
                    "completed_upload_jobs": completed_upload_jobs,
                    "prediction_refresh": prediction_refresh,
                    "updated_in_place": True,
                    "status": "done",
                },
                status.HTTP_200_OK,
            )
        except Exception as exc:
            logger.error(
                "Failed to re-import video %s: %s",
                self.video_hash,
                exc,
                exc_info=True,
            )

            error_msg = str(exc)
            if any(
                phrase in error_msg.lower()
                for phrase in ["insufficient storage", "no space left", "disk full"]
            ):
                return (
                    {
                        "error": f"Storage error during re-import: {error_msg}",
                        "error_type": "storage_error",
                        "video_id": self.video_id,
                        "uuid": self.video_hash,
                    },
                    status.HTTP_507_INSUFFICIENT_STORAGE,
                )

            return (
                {
                    "error": f"Re-import failed: {error_msg}",
                    "error_type": "processing_error",
                    "video_id": self.video_id,
                    "uuid": self.video_hash,
                },
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _run_video_import_service(self) -> int:
        raw_file = _video_raw_file(self.video)
        if raw_file is None:
            raise FileNotFoundError("Raw video source is missing.")

        with ensure_local_file(raw_file) as raw_file_path:
            with transaction.atomic():
                reset_upload_jobs = _reset_reimport_state(self.video)

            logger.info(
                "Starting VideoImportService re-anonymization for %s",
                self.video_hash,
            )
            self.video_service.reanonymize_existing_video(
                self.video,
                source_path=raw_file_path,
            )
        return reset_upload_jobs

    def _maybe_dispatch_prediction_refresh(self) -> dict[str, Any]:
        if not _as_bool(self.payload.get("refresh_predictions"), default=True):
            return {
                "status": "skipped",
                "queued": False,
                "reason": "disabled",
            }

        try:
            return video_reimport_json_safe_dict(
                _dispatch_prediction_refresh(self.video, dict(self.payload))
            )
        except (
            AiModel.DoesNotExist,
            ModelMeta.DoesNotExist,
            TemporalInferenceConfigError,
            ValueError,
        ) as exc:
            logger.warning(
                "Video re-import completed but prediction refresh was not queued "
                "for video %s: %s",
                self.video_hash,
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
                self.video_hash,
            )
            return {
                "status": "failed",
                "queued": False,
                "error": str(exc),
            }


__all__ = ["VideoReimportOrchestrator", "VideoReimportResponse"]

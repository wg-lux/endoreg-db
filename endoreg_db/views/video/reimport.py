from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from lx_dtypes.models.contracts.video_reimport import (
    VideoReimportApiResponsePayload,
    VideoReimportRequestData,
    dump_video_reimport_api_response,
    dump_video_reimport_request_payload,
    validate_video_reimport_request_payload,
)
from pydantic import ValidationError as PydanticValidationError
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.views.access_control import (
    CenterScopedVideoPermission,
)

from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.video_import import VideoImportService
from endoreg_db.services.video_reimport_orchestrator import VideoReimportOrchestrator
from endoreg_db.views.reimport_helpers import request_payload_dict

logger = logging.getLogger(__name__)


def _video_hash(video: VideoFile) -> str:
    return str(getattr(video, "video_hash", ""))


def _api_response(payload: Mapping[str, Any], *, status_code: int) -> Response:
    response_payload = VideoReimportApiResponsePayload.model_validate(dict(payload))
    return Response(
        dump_video_reimport_api_response(response_payload),
        status=status_code,
    )


def _request_payload(request: object) -> VideoReimportRequestData:
    payload = validate_video_reimport_request_payload(request_payload_dict(request))
    return dump_video_reimport_request_payload(payload)


class VideoReimportView(APIView):
    """
    API endpoint to re-import a video file and regenerate metadata.
    This is useful when OCR failed or metadata is incomplete.
    """

    permission_classes = [
        EnvironmentAwarePermission,
        PolicyPermission,
        CenterScopedVideoPermission,
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.video_service = VideoImportService()

    def post(self, request: Request, pk: object) -> Response:
        """
        Re-import a video file to regenerate SensitiveMeta and other metadata.
        Instead of creating a new video, this updates the existing one.

        Args:
            pk (int): Primary key of the VideoFile to reimport
        """
        if not isinstance(pk, int) or pk <= 0:
            return _api_response(
                {"error": "Invalid video ID provided."},
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        try:
            video = VideoFile.objects.select_related(
                "center",
                "processor",
                "video_meta__processor",
            ).get(id=pk)
            video_hash = _video_hash(video)
            logger.info("Found video %s (ID: %s) for re-import", video_hash, pk)
        except VideoFile.DoesNotExist:
            logger.warning("Video with ID %s not found", pk)
            return _api_response(
                {"error": f"Video with ID {pk} not found."},
                status_code=status.HTTP_404_NOT_FOUND,
            )

        try:
            payload = _request_payload(request)
        except PydanticValidationError as exc:
            return _api_response(
                {
                    "status": "failed",
                    "operation": "video_reimport",
                    "reason": str(exc),
                    "error": "Invalid video re-import payload.",
                    "error_type": "validation_error",
                    "video_id": pk,
                    "uuid": _video_hash(video),
                    "updated_in_place": True,
                },
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        response_payload, status_code = VideoReimportOrchestrator(
            video=video,
            video_id=pk,
            payload=payload,
            video_service=self.video_service,
        ).run()

        return _api_response(response_payload, status_code=status_code)


__all__ = ["VideoReimportView"]

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeAlias

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import (
    BasePermission,
    OperandHolder,
    SingleOperandHolder,
)
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from lx_dtypes.models.meta.VideoMetadata import (
    VideoFpsDetailsPayload,
    VideoFpsErrorPayload,
    VideoFpsPayload,
    VideoFpsStatsPayload,
)

from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.video_files import get_video_fps
from endoreg_db.utils.web.permissions import EnvironmentAwarePermission

PermissionClass: TypeAlias = type[BasePermission] | OperandHolder | SingleOperandHolder


class VideoFpsView(APIView):
    """
    GET media/videos/{pk}/fps/ - Return fps for a single video.
    """

    permission_classes: Sequence[PermissionClass] = (EnvironmentAwarePermission,)

    def get(self, request: Request, pk: int) -> Response[dict[str, object]]:
        video = get_object_or_404(VideoFile, pk=pk)
        try:
            fps = float(get_video_fps(video))
        except Exception as exc:
            error_payload: VideoFpsPayload = VideoFpsErrorPayload(
                error="Could not determine fps for the requested video.",
                details=VideoFpsDetailsPayload(video_id=pk, reason=str(exc)),
            )
            return Response(
                error_payload.model_dump(mode="json"),
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        success_payload: VideoFpsPayload = VideoFpsStatsPayload(
            video_id=pk,
            fps=fps,
        )
        return Response(
            success_payload.model_dump(mode="json"),
            status=status.HTTP_200_OK,
        )

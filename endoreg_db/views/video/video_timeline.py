from __future__ import annotations

from collections.abc import Sequence
from typing import TypeAlias

from django.shortcuts import get_object_or_404
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from rest_framework import status
from rest_framework.permissions import (
    BasePermission,
    OperandHolder,
    SingleOperandHolder,
)
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.video_files import get_video_frame_neighborhood
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.views.access_control import (
    CenterScopedVideoPermission,
    assert_anonymized_center_scope_allowed,
)

PermissionClass: TypeAlias = type[BasePermission] | OperandHolder | SingleOperandHolder


class FrameNeighborhoodQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: float = Field(ge=0)
    radius: int = Field(default=12, ge=1, le=50)


class VideoFrameNeighborhoodView(APIView):
    """Return the PTS-backed previous/current/next display-frame boundaries."""

    permission_classes: Sequence[PermissionClass] = (
        EnvironmentAwarePermission,
        PolicyPermission,
        CenterScopedVideoPermission,
    )

    def get(self, request: Request, pk: int) -> Response:
        try:
            query = FrameNeighborhoodQuery.model_validate(request.query_params.dict())
        except ValidationError as exc:
            return Response(
                {
                    "error": "Invalid frame-neighborhood query.",
                    "details": exc.errors(include_context=False),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        video = get_object_or_404(VideoFile, pk=pk)
        assert_anonymized_center_scope_allowed(request=request, obj=video)
        self.check_object_permissions(request, video)
        try:
            neighborhood = get_video_frame_neighborhood(
                video,
                query.timestamp,
                radius=query.radius,
            )
        except (TypeError, ValueError) as exc:
            return Response(
                {
                    "error": "Frame neighborhood is unavailable.",
                    "details": str(exc),
                    "video_id": pk,
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        return Response(neighborhood.to_dict(), status=status.HTTP_200_OK)

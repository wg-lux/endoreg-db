from __future__ import annotations

from typing import cast

from django.shortcuts import get_object_or_404
from lx_dtypes.models.contracts.export_ready import (
    VideoReadyForExportPayload,
    dump_video_ready_for_export_payload,
)
from pydantic import ValidationError as PydanticValidationError
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.export_ready import (
    ReadyForExportError,
    mark_video_ready_for_export,
)
from endoreg_db.utils.pydantic_drf import drf_validation_error_detail
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.views.access_control import (
    CenterScopedVideoPermission,
    assert_center_scope_allowed,
)


class VideoMarkReadyForExportView(APIView):
    permission_classes = [
        EnvironmentAwarePermission,
        PolicyPermission,
        CenterScopedVideoPermission,
    ]

    def post(self, request: Request, pk: int) -> Response:
        video = get_object_or_404(
            VideoFile.objects.select_related("center", "state"),
            pk=pk,
        )
        assert_center_scope_allowed(request=request, obj=video)
        try:
            payload = dump_video_ready_for_export_payload(
                VideoReadyForExportPayload.model_validate(cast(object, request.data))
            )
        except PydanticValidationError as exc:
            return Response(
                drf_validation_error_detail(exc),
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = mark_video_ready_for_export(
                video=video,
                user=request.user,
                center_key=payload["center_key"],
                expected_processed_file_sha256=payload["processed_file_sha256"],
            )
        except ReadyForExportError as exc:
            return Response(
                {"success": False, "error": str(exc)},
                status=exc.status_code,
            )

        return Response({"success": True, **result.to_dict()})

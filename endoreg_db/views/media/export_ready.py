from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from endoreg_db.models import VideoFile
from endoreg_db.services.export_ready import (
    ReadyForExportError,
    mark_video_ready_for_export,
)
from endoreg_db.utils.web.permissions import EnvironmentAwarePermission


class VideoMarkReadyForExportView(APIView):
    permission_classes = [EnvironmentAwarePermission]

    def post(self, request, pk: int):
        video = get_object_or_404(
            VideoFile.objects.select_related("center", "state"),
            pk=pk,
        )
        payload = request.data or {}
        try:
            result = mark_video_ready_for_export(
                video=video,
                user=request.user,
                center_key=payload.get("center_key"),
                expected_processed_file_sha256=payload.get("processed_file_sha256"),
            )
        except ReadyForExportError as exc:
            return Response(
                {"success": False, "error": str(exc)},
                status=exc.status_code,
            )

        return Response({"success": True, **result.to_dict()})

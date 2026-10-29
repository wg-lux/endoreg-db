"""HTTP adapter that hands completed export runs to the browser.

The browser writes the listed files into a folder the user picks, or saves the
ZIP archive where it lacks a folder picker. Access rules live in
``endoreg_db.services.media.frame_annotation_export_downloads``. With Nginx
offload, single files leave via X-Accel-Redirect so frame bytes never occupy
an application worker; the archive is assembled on the fly and stays here.
"""

from __future__ import annotations

import mimetypes
from typing import cast
from uuid import UUID

from django.contrib.auth.models import AnonymousUser, User
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import HttpResponseBase, StreamingHttpResponse
from rest_framework import serializers, status
from rest_framework.request import Request
from rest_framework.response import Response

from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.openapi import OpenApiAPIView
from endoreg_db.services.media.frame_annotation_export_downloads import (
    CompletedExportRun,
    ExportRunNotFound,
    ExportRunUnavailable,
    completed_export_run,
    iter_export_run_archive,
    list_export_run_files,
    log_export_run_access,
    resolve_export_run_file,
)
from endoreg_db.utils.nginx_accel import (
    build_nginx_accel_response_for_path,
    nginx_offload_enabled,
)
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.utils.storage_streaming import (
    BoundedStreamingHttpResponse,
    build_partial_content_response_from_path,
)


class FrameAnnotationExportRunFileSerializer(serializers.Serializer[dict[str, object]]):
    path = serializers.CharField()
    size = serializers.IntegerField()


class FrameAnnotationExportRunFilesSerializer(
    serializers.Serializer[dict[str, object]]
):
    job_id = serializers.UUIDField()
    output_dir = serializers.CharField()
    files = FrameAnnotationExportRunFileSerializer(many=True)


def _user(request: Request) -> User | AnonymousUser:
    return cast(User | AnonymousUser, request.user)


def _run_or_error(request: Request, job_id: UUID) -> CompletedExportRun | Response:
    try:
        return completed_export_run(job_id, user=_user(request))
    except ExportRunNotFound:
        return Response(
            {"error": "Export job not found."}, status=status.HTTP_404_NOT_FOUND
        )
    except ExportRunUnavailable as exc:
        return Response({"error": str(exc)}, status=status.HTTP_409_CONFLICT)


def _private[R: HttpResponseBase](response: R) -> R:
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


class FrameAnnotationExportRunFilesView(OpenApiAPIView):
    permission_classes = [EnvironmentAwarePermission, PolicyPermission]
    serializer_class = FrameAnnotationExportRunFilesSerializer

    def get(self, request: Request, job_id: UUID) -> Response:
        run = _run_or_error(request, job_id)
        if isinstance(run, Response):
            return run
        try:
            files = list_export_run_files(run)
        except ExportRunUnavailable as exc:
            return Response({"error": str(exc)}, status=status.HTTP_409_CONFLICT)
        response = Response(
            {
                "job_id": run.job.pk,
                "output_dir": run.job.output_dir,
                "files": [
                    {"path": entry.relative_path, "size": entry.size} for entry in files
                ],
            }
        )
        response["Cache-Control"] = "private, no-store"
        return response


class FrameAnnotationExportRunFileView(OpenApiAPIView):
    permission_classes = [EnvironmentAwarePermission, PolicyPermission]

    def get(
        self, request: Request, job_id: UUID, relative_path: str
    ) -> HttpResponseBase:
        run = _run_or_error(request, job_id)
        if isinstance(run, Response):
            return run
        try:
            path = resolve_export_run_file(run, relative_path)
        except DjangoValidationError as exc:
            return Response(
                {"errors": exc.messages}, status=status.HTTP_400_BAD_REQUEST
            )
        except ExportRunNotFound:
            return Response(
                {"error": "Export file not found."}, status=status.HTTP_404_NOT_FOUND
            )
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if nginx_offload_enabled():
            # Nginx answers Range requests itself from the internal location.
            log_export_run_access(run, user=_user(request), access="file", file_count=1)
            return _private(
                build_nginx_accel_response_for_path(
                    path=path,
                    content_type=content_type,
                    filename=path.name,
                    disposition="attachment",
                )
            )
        size = path.stat().st_size
        try:
            response = build_partial_content_response_from_path(
                file_path=path,
                content_type=content_type,
                file_size=size,
                range_header=request.headers.get("Range"),
                disposition="attachment",
                filename=path.name,
            )
        except ValueError:
            error = Response(
                {"error": "Invalid byte range."},
                status=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            )
            error["Content-Range"] = f"bytes */{size}"
            return error
        log_export_run_access(run, user=_user(request), access="file", file_count=1)
        return _private(response)


class FrameAnnotationExportRunArchiveView(OpenApiAPIView):
    permission_classes = [EnvironmentAwarePermission, PolicyPermission]

    def get(self, request: Request, job_id: UUID) -> StreamingHttpResponse | Response:
        run = _run_or_error(request, job_id)
        if isinstance(run, Response):
            return run
        try:
            files = list_export_run_files(run)
        except ExportRunUnavailable as exc:
            return Response({"error": str(exc)}, status=status.HTTP_409_CONFLICT)
        response = BoundedStreamingHttpResponse(
            iter_export_run_archive(run, files), content_type="application/zip"
        )
        response["Content-Disposition"] = (
            f'attachment; filename="{run.job.output_dir}.zip"'
        )
        log_export_run_access(
            run, user=_user(request), access="archive", file_count=len(files)
        )
        return _private(response)

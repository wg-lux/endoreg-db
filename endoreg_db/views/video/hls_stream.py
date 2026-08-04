from __future__ import annotations

from pathlib import Path
from uuid import UUID

from django.http import FileResponse, Http404, HttpResponse
from django.http.response import HttpResponseBase
from django.db import transaction
from rest_framework.request import Request
from rest_framework.views import APIView

from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.config.env import nginx_offload_enabled
from endoreg_db.models.media.video.hls_artifact import VideoHlsArtifact
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.hls_media import (
    coerce_hls_artifact_kind,
    get_ready_hls_artifact,
    get_ready_hls_artifact_by_key,
    hls_playlist_path,
    hls_segment_path,
    unwrap_hls_content_key,
)
from endoreg_db.services.media_operation_gate import create_video_stream_lease
from endoreg_db.services.video_files import VideoArtifactKind
from endoreg_db.utils.cors import resolve_response_origin
from endoreg_db.utils.nginx_accel import build_nginx_accel_response_for_path
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.utils.storage_streaming import add_cors_headers
from endoreg_db.views.access_control import (
    CenterScopedVideoPermission,
    assert_anonymized_center_scope_allowed,
    assert_center_scope_allowed,
)
from endoreg_db.views.video.lookups import get_video_or_404 as _get_video_or_404

HLS_PLAYLIST_CONTENT_TYPE = "application/vnd.apple.mpegurl"
HLS_SEGMENT_CONTENT_TYPE = "video/mp2t"
HLS_KEY_CONTENT_TYPE = "application/octet-stream"
HLS_SEGMENT_CACHE_CONTROL = "private, max-age=31536000, immutable"


def _private_no_store(response: HttpResponseBase) -> HttpResponseBase:
    response["Cache-Control"] = "no-store, private"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


def _harden_media_response(response: HttpResponseBase) -> HttpResponseBase:
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _add_cors_if_configured(
    response: HttpResponseBase,
    request: Request,
) -> HttpResponseBase:
    frontend_origin = resolve_response_origin(request)
    if frontend_origin is None:
        return response
    return add_cors_headers(response, frontend_origin)


def _artifact_kind_from_request(request: Request) -> VideoArtifactKind:
    raw_value = request.query_params.get("type") or request.query_params.get(
        "file_type"
    )
    if raw_value is None:
        return VideoArtifactKind.PROCESSED
    try:
        return coerce_hls_artifact_kind(raw_value)
    except ValueError as exc:
        raise Http404(str(exc)) from exc


def _file_response(path: Path, *, content_type: str) -> FileResponse:
    return FileResponse(path.open("rb"), content_type=content_type)


class HLSPlaylistView(APIView):
    permission_classes = [
        EnvironmentAwarePermission,
        PolicyPermission,
        CenterScopedVideoPermission,
    ]

    def get(
        self,
        request: Request,
        pk: int | str | None = None,
    ) -> HttpResponseBase:
        video = _get_video_or_404(pk)
        artifact_kind = _artifact_kind_from_request(request)
        if artifact_kind == VideoArtifactKind.PROCESSED:
            assert_anonymized_center_scope_allowed(request=request, obj=video)
        else:
            assert_center_scope_allowed(request=request, obj=video)
        self.check_object_permissions(request, video)
        try:
            with transaction.atomic():
                locked_video = VideoFile.objects.select_for_update().get(pk=video.pk)
                artifact = get_ready_hls_artifact(
                    video=locked_video,
                    artifact_kind=artifact_kind,
                )
                path = hls_playlist_path(artifact)
                create_video_stream_lease(
                    locked_video,
                    file_type=f"hls_{artifact.artifact_kind}_playlist",
                )
        except (FileNotFoundError, ValueError, VideoHlsArtifact.DoesNotExist):
            raise Http404("HLS playlist is not available") from None

        if nginx_offload_enabled():
            response = build_nginx_accel_response_for_path(
                path=path,
                content_type=HLS_PLAYLIST_CONTENT_TYPE,
                filename=path.name,
                disposition="inline",
                frontend_origin=resolve_response_origin(request),
                accept_ranges=False,
            )
        else:
            response = _file_response(path, content_type=HLS_PLAYLIST_CONTENT_TYPE)
            response = _add_cors_if_configured(response, request)
        return _harden_media_response(_private_no_store(response))


class HLSKeyView(APIView):
    permission_classes = [
        EnvironmentAwarePermission,
        PolicyPermission,
        CenterScopedVideoPermission,
    ]

    def get(
        self,
        request: Request,
        pk: int | str | None = None,
        key_id: UUID | None = None,
    ) -> HttpResponseBase:
        if key_id is None:
            raise Http404("HLS key ID is required")
        video = _get_video_or_404(pk)
        self.check_object_permissions(request, video)
        try:
            with transaction.atomic():
                locked_video = VideoFile.objects.select_for_update().get(pk=video.pk)
                artifact = get_ready_hls_artifact_by_key(
                    video=locked_video,
                    key_id=key_id,
                )
                key = unwrap_hls_content_key(artifact)
                if artifact.artifact_kind == VideoArtifactKind.PROCESSED:
                    assert_anonymized_center_scope_allowed(
                        request=request, obj=locked_video
                    )
                else:
                    assert_center_scope_allowed(request=request, obj=locked_video)
                create_video_stream_lease(
                    locked_video,
                    file_type=f"hls_{artifact.artifact_kind}_key",
                )
        except (FileNotFoundError, ValueError, VideoHlsArtifact.DoesNotExist):
            raise Http404("HLS key is not available") from None

        response = HttpResponse(key, content_type=HLS_KEY_CONTENT_TYPE)
        response["Content-Length"] = str(len(key))
        response = _add_cors_if_configured(response, request)
        return _harden_media_response(_private_no_store(response))


class HLSSegmentView(APIView):
    permission_classes = [
        EnvironmentAwarePermission,
        PolicyPermission,
        CenterScopedVideoPermission,
    ]

    def get(
        self,
        request: Request,
        pk: int | str | None = None,
        key_id: UUID | None = None,
        segment_name: str = "",
    ) -> HttpResponseBase:
        if key_id is None:
            raise Http404("HLS key ID is required")
        video = _get_video_or_404(pk)
        self.check_object_permissions(request, video)
        if not nginx_offload_enabled():
            raise Http404("HLS segment offload is not configured")
        try:
            with transaction.atomic():
                locked_video = VideoFile.objects.select_for_update().get(pk=video.pk)
                artifact = get_ready_hls_artifact_by_key(
                    video=locked_video,
                    key_id=key_id,
                )
                path = hls_segment_path(artifact, segment_name)
                if artifact.artifact_kind == VideoArtifactKind.PROCESSED:
                    assert_anonymized_center_scope_allowed(
                        request=request, obj=locked_video
                    )
                else:
                    assert_center_scope_allowed(request=request, obj=locked_video)
                create_video_stream_lease(
                    locked_video,
                    file_type=f"hls_{artifact.artifact_kind}_segment",
                )
        except (FileNotFoundError, ValueError, VideoHlsArtifact.DoesNotExist):
            raise Http404("HLS segment is not available") from None

        response = build_nginx_accel_response_for_path(
            path=path,
            content_type=HLS_SEGMENT_CONTENT_TYPE,
            filename=path.name,
            disposition="inline",
            frontend_origin=resolve_response_origin(request),
        )
        response["Cache-Control"] = HLS_SEGMENT_CACHE_CONTROL
        return _harden_media_response(response)

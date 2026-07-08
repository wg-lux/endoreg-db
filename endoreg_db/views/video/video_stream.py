"""
Legacy video playback compatibility views.

Legacy endpoints:
- /api/media/videos/<pk>/stream/
- /api/media/videos/<pk>/ (legacy alias)

They redirect to processed HLS:
- /api/media/videos/<pk>/hls/playlist.m3u8
"""

from __future__ import annotations

import logging

from django.http import HttpResponseRedirect
from django.http.response import HttpResponseBase
from rest_framework.request import Request
from rest_framework.views import APIView

from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.utils.cors import resolve_response_origin
from endoreg_db.utils.media_urls import (
    build_absolute_media_url,
    build_video_hls_playlist_path,
)
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.utils.storage_streaming import add_cors_headers
from endoreg_db.views.video.lookups import get_video_or_404

logger = logging.getLogger(__name__)

LEGACY_VIDEO_STREAM_STATE = "hls_compat_redirect"


def _add_cors_headers_if_configured(
    response: HttpResponseBase, frontend_origin: str | None
) -> HttpResponseBase:
    if frontend_origin is None:
        return response
    return add_cors_headers(response, frontend_origin)


class VideoStreamView(APIView):
    permission_classes = [EnvironmentAwarePermission, PolicyPermission]

    @staticmethod
    def _get_video_or_404(pk: int | str | None) -> VideoFile:
        return get_video_or_404(pk)

    def get(
        self,
        request: Request,
        pk: int | str | None = None,
    ) -> HttpResponseBase:
        video = self._get_video_or_404(pk)
        self.check_object_permissions(request, video)

        hls_playlist_url = build_absolute_media_url(
            request,
            build_video_hls_playlist_path(int(video.pk), file_type="processed"),
        )
        logger.info(
            "Redirecting legacy video stream URL to processed HLS for video id=%s",
            getattr(video, "pk", None),
        )

        response = HttpResponseRedirect(hls_playlist_url)
        response["X-Stream-State"] = LEGACY_VIDEO_STREAM_STATE
        response["Link"] = (
            f'<{hls_playlist_url}>; rel="alternate"; '
            'type="application/vnd.apple.mpegurl"'
        )
        return _add_cors_headers_if_configured(
            response,
            resolve_response_origin(request),
        )

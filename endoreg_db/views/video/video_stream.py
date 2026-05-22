"""
Video streaming views.

Canonical binary endpoints:
- /api/media/videos/<pk>/stream/
- /api/media/videos/<pk>/ (legacy alias)
"""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from django.http import Http404, HttpResponse
from rest_framework.views import APIView

from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.config.env import raw_django_streaming_enabled
from endoreg_db.models import VideoFile
from endoreg_db.models.media.video.storage_mode import (
    VideoStorageMode,
    coerce_video_storage_mode,
)
from endoreg_db.services.streamable_media import sync_video_streamable_artifacts
from endoreg_db.services.video_files import (
    VideoArtifactKind,
    get_active_raw_video_file,
    get_video_stream_relative_path,
    parse_video_artifact_kind,
    resolve_video_stream_source,
)
from endoreg_db.services.media_operation_gate import (
    create_video_stream_lease,
    release_media_operation_lease,
    wrap_iterator_with_media_lease,
)
from endoreg_db.utils.web.cors import resolve_response_origin
from endoreg_db.utils.encryption.encrypted import MAGIC as LX_ENCRYPTED_MAGIC
from endoreg_db.utils.web.nginx_accel import (
    build_nginx_accel_response,
    nginx_offload_enabled,
)
from endoreg_db.utils.filesystem.paths import resolve_existing_protected_media_path
from endoreg_db.utils.web.permissions import EnvironmentAwarePermission
from endoreg_db.utils.storage.streaming import (
    add_cors_headers,
    build_partial_content_response,
    build_partial_content_response_from_path,
    field_file_size,
    maybe_local_plaintext_path,
    parse_byte_range,
)

logger = logging.getLogger(__name__)


def _path_starts_with_encrypted_magic(path: Path) -> bool:
    with path.open("rb") as handle:
        return handle.read(len(LX_ENCRYPTED_MAGIC)) == LX_ENCRYPTED_MAGIC


def _video_uses_streamable_mode(video: VideoFile) -> bool:
    try:
        return (
            coerce_video_storage_mode(getattr(video, "storage_mode", None))
            == VideoStorageMode.STREAMABLE
        )
    except ValueError:
        return False


def _resolve_verified_streamable_path(
    relative_path: str | None,
) -> tuple[Path | None, str | None]:
    if relative_path is None:
        return None, "missing_streamable_artifact"

    resolved_stream_path = resolve_existing_protected_media_path(relative_path)
    if resolved_stream_path is None:
        return None, "missing_streamable_artifact"

    try:
        stat_result = resolved_stream_path.stat()
    except OSError as exc:
        logger.warning(
            "Refusing unreadable streamable video artifact: path=%s error=%s",
            resolved_stream_path,
            exc,
        )
        return None, "unreadable_streamable_artifact"

    if not resolved_stream_path.is_file() or stat_result.st_size <= 0:
        logger.warning(
            "Refusing invalid streamable video artifact: path=%s size=%s",
            resolved_stream_path,
            stat_result.st_size,
        )
        return None, "invalid_streamable_artifact"

    try:
        starts_with_magic = _path_starts_with_encrypted_magic(resolved_stream_path)
    except OSError as exc:
        logger.warning(
            "Refusing unreadable streamable video artifact: path=%s error=%s",
            resolved_stream_path,
            exc,
        )
        return None, "unreadable_streamable_artifact"

    if starts_with_magic:
        logger.error(
            "Refusing streamable video artifact because it is encrypted: %s",
            resolved_stream_path,
        )
        return None, "encrypted_streamable_artifact"

    return resolved_stream_path, None


def _try_repair_streamable_artifact(
    video: VideoFile,
    artifact_kind: VideoArtifactKind,
) -> str:
    try:
        sync_video_streamable_artifacts(
            video,
            include_raw=artifact_kind == VideoArtifactKind.RAW,
            include_processed=artifact_kind == VideoArtifactKind.PROCESSED,
            save=True,
        )
    except Exception as exc:
        logger.warning(
            "Could not repair streamable video artifact for id=%s type=%s: %s",
            getattr(video, "pk", None),
            artifact_kind.value,
            exc,
            exc_info=True,
        )
        return "streamable_repair_failed"

    return "streamable_repaired"


def _field_file_for_stream(video: VideoFile, artifact_kind: VideoArtifactKind):
    if artifact_kind == VideoArtifactKind.PROCESSED:
        return getattr(video, "processed_file", None)
    try:
        return get_active_raw_video_file(video)
    except (FileNotFoundError, ValueError):
        return None


class VideoStreamView(APIView):
    permission_classes = [EnvironmentAwarePermission, PolicyPermission]

    @staticmethod
    def _parse_file_type(request) -> VideoArtifactKind:
        raw_value = request.query_params.get("type") or request.query_params.get(
            "file_type"
        )
        if raw_value is None:
            return VideoArtifactKind.RAW
        return parse_video_artifact_kind(
            raw_value,
            default=VideoArtifactKind.PROCESSED,
        )

    @staticmethod
    def _get_video_or_404(pk) -> VideoFile:
        if pk is None:
            raise Http404("Video ID is required")
        try:
            video_id = int(pk)
        except (TypeError, ValueError) as exc:
            raise Http404("Invalid video ID format") from exc

        try:
            return VideoFile.objects.get(pk=video_id)
        except VideoFile.DoesNotExist as exc:
            raise Http404(f"Video with ID {pk} not found") from exc

    def get(self, request, pk=None):
        video = self._get_video_or_404(pk)
        self.check_object_permissions(request, video)

        artifact_kind = self._parse_file_type(request)
        file_type = artifact_kind.value
        frontend_origin = resolve_response_origin(request)
        range_header = request.headers.get("Range") or request.META.get("HTTP_RANGE")

        stream_state = None
        stream_relative_path = get_video_stream_relative_path(video, artifact_kind)
        if _video_uses_streamable_mode(video):
            resolved_stream_path, stream_state = _resolve_verified_streamable_path(
                stream_relative_path
            )

            if nginx_offload_enabled():
                if resolved_stream_path is None:
                    repair_state = _try_repair_streamable_artifact(
                        video,
                        artifact_kind,
                    )
                    stream_relative_path = get_video_stream_relative_path(
                        video,
                        artifact_kind,
                    )
                    resolved_stream_path, unresolved_state = (
                        _resolve_verified_streamable_path(stream_relative_path)
                    )
                    stream_state = unresolved_state or repair_state

                if (
                    resolved_stream_path is not None
                    and stream_relative_path is not None
                ):
                    content_type = (
                        mimetypes.guess_type(stream_relative_path)[0] or "video/mp4"
                    )
                    stream_lease = create_video_stream_lease(
                        video,
                        file_type=file_type,
                    )
                    try:
                        response = build_nginx_accel_response(
                            protected_relative_path=stream_relative_path,
                            content_type=content_type,
                            filename=Path(stream_relative_path).name,
                            disposition="inline",
                            frontend_origin=frontend_origin,
                        )
                    except Exception:
                        release_media_operation_lease(stream_lease)
                        raise
                    if stream_lease is not None:
                        response["X-Media-Operation-Lease"] = str(stream_lease.token)
                    return response

        try:
            field_file, local_path = resolve_video_stream_source(
                video,
                artifact_kind,
                materialize_if_missing=False,
            )
        except (FileNotFoundError, ValueError) as exc:
            field_file = _field_file_for_stream(video, artifact_kind)
            local_path = (
                maybe_local_plaintext_path(field_file)
                if field_file is not None
                else None
            )
            if local_path is None:
                logger.warning(
                    "Video stream source unavailable for id=%s type=%s: %s",
                    getattr(video, "pk", None),
                    file_type,
                    exc,
                )
                raise Http404("Video file is not available") from exc

        filename = (
            local_path.name if local_path is not None else Path(field_file.name).name
        )
        content_type = (
            mimetypes.guess_type(
                str(local_path if local_path is not None else field_file.name)
            )[0]
            or "video/mp4"
        )

        if (
            file_type == "raw"
            and local_path is None
            and not raw_django_streaming_enabled()
        ):
            logger.warning(
                "Refusing raw Django video streaming fallback for id=%s; "
                "streamable artifact is required",
                getattr(video, "pk", None),
            )
            response = HttpResponse(status=409, content_type="text/plain")
            response["X-Stream-State"] = stream_state or "raw_django_streaming_disabled"
            return add_cors_headers(response, frontend_origin)

        try:
            file_size = (
                local_path.stat().st_size
                if local_path is not None
                else field_file_size(field_file)
            )
        except FileNotFoundError as exc:
            logger.warning(
                "Video file disappeared during streaming setup for id=%s type=%s: %s",
                getattr(video, "pk", None),
                file_type,
                exc,
            )
            raise Http404("Video file is not available") from exc

        if file_size <= 0:
            raise Http404("Video file is empty")

        if range_header:
            try:
                parse_byte_range(range_header, file_size)
            except ValueError:
                response = HttpResponse(status=416, content_type=content_type)
                response["Content-Range"] = f"bytes */{file_size}"
                response["Accept-Ranges"] = "bytes"
                return add_cors_headers(response, frontend_origin)

        if local_path is not None:
            response = build_partial_content_response_from_path(
                file_path=local_path,
                content_type=content_type,
                file_size=file_size,
                range_header=range_header,
                disposition="inline",
                filename=filename,
            )
        else:
            response = build_partial_content_response(
                field_file=field_file,
                content_type=content_type,
                file_size=file_size,
                range_header=range_header,
                disposition="inline",
                filename=filename,
            )

        stream_lease = create_video_stream_lease(video, file_type=file_type)
        if stream_lease is not None:
            response.streaming_content = wrap_iterator_with_media_lease(
                response.streaming_content,
                stream_lease,
            )
            response["X-Media-Operation-Lease"] = str(stream_lease.token)

        if stream_state is not None:
            response["X-Stream-State"] = stream_state

        return add_cors_headers(response, frontend_origin)

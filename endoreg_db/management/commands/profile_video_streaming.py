from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast
from uuid import UUID

from django.contrib.auth.models import Group, User
from django.core.handlers.wsgi import WSGIRequest
from django.core.management.base import CommandError, CommandParser
from django.db import transaction
from django.http.response import HttpResponseBase
from rest_framework.test import APIRequestFactory, force_authenticate

from endoreg_db.config.env import get_protected_media_url, nginx_offload_enabled
from endoreg_db.management.commands._profiling import (
    add_profiling_arguments,
    command_profiling_config_from_options,
    profiling_metadata,
    run_with_optional_profile,
)
from endoreg_db.models.media.operation_lease import MediaOperationLease
from endoreg_db.models.media.video.hls_artifact import VideoHlsArtifact
from endoreg_db.models.media.video.storage_mode import VideoStorageMode
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.hls_media import HLS_CONTENT_KEY_BYTES, hls_playlist_path
from endoreg_db.services.video_files import (
    VideoArtifactKind,
    get_video_stream_relative_path,
)
from endoreg_db.utils.paths import resolve_existing_protected_media_path
from endoreg_db.views.video.hls_stream import (
    HLSKeyView,
    HLSPlaylistView,
    HLSSegmentView,
)
from endoreg_db.views.video.video_stream import VideoStreamView

from ._video_command_base import BaseVideoCommand

type _Endpoint = Literal["hls", "mp4", "all"]
type _FrontendHlsSupport = Literal["hlsjs", "native", "none"]
type _FrontendPlaybackMode = Literal["hls", "native_hls", "progressive", "error"]

HLS_PLAYLIST_ACCEPT = "application/vnd.apple.mpegurl, application/x-mpegURL, */*"
FRONTEND_PLAYBACK_MODES: tuple[_FrontendPlaybackMode, ...] = (
    "hls",
    "native_hls",
    "progressive",
    "error",
)


class _GroupRelation(Protocol):
    def add(self, *objs: Group | int) -> None: ...


class _UserWithGroups(Protocol):
    groups: _GroupRelation


class _HlsArtifactStreamingFields(Protocol):
    key_id: UUID
    segment_directory_relative_path: str


@dataclass(frozen=True)
class _CommandOptions:
    endpoint: _Endpoint
    video_ids: list[int] | None
    limit: int | None
    iterations: int
    user_id: int | None
    origin: str | None
    frontend_hls_support: _FrontendHlsSupport
    json_output: bool

    @property
    def profiles_hls(self) -> bool:
        return self.endpoint in {"hls", "all"}

    @property
    def profiles_mp4(self) -> bool:
        return self.endpoint in {"mp4", "all"}


@dataclass(frozen=True)
class _HlsStreamingTarget:
    video_id: int
    key_id: UUID
    segment_name: str


@dataclass(frozen=True)
class _Mp4StreamingTarget:
    video_id: int
    relative_path: str


@dataclass(frozen=True)
class _StreamingTargets:
    hls: tuple[_HlsStreamingTarget, ...]
    mp4: tuple[_Mp4StreamingTarget, ...]

    @property
    def empty(self) -> bool:
        return not self.hls and not self.mp4


class Command(BaseVideoCommand):
    help = (
        "Profile processed video streaming request handling through the NGINX "
        "handoff path. The command exercises DRF views and validates "
        "X-Accel-Redirect responses without streaming media bytes through Django."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        self.add_video_selection_arguments(
            parser,
            limit_help="Maximum number of selected HLS artifacts/videos per endpoint.",
        )
        parser.add_argument(
            "--endpoint",
            choices=("hls", "mp4", "all"),
            default="hls",
            help=(
                "Streaming endpoint to profile. hls profiles playlist/key/segment "
                "requests; mp4 verifies that the retired MP4 stream endpoint is gone."
            ),
        )
        parser.add_argument(
            "--iterations",
            type=int,
            default=50,
            help="Number of request batches to run for each selected target.",
        )
        parser.add_argument(
            "--user-id",
            type=int,
            default=None,
            help=(
                "Existing authenticated user id for permission checks. If omitted, "
                "a temporary profiler user is created inside a rolled-back transaction."
            ),
        )
        parser.add_argument(
            "--origin",
            default=None,
            help="Optional Origin header used to include configured CORS work.",
        )
        parser.add_argument(
            "--frontend-hls-support",
            choices=("hlsjs", "native", "none"),
            default="hlsjs",
            help=(
                "Simulated frontend HLS capability. hlsjs matches Chromium-like "
                "lx-annotate clients, native matches Safari-like clients, none "
                "verifies that retired progressive fallback fails closed."
            ),
        )
        self.add_json_output_argument(parser)
        add_profiling_arguments(parser)

    def handle(self, *args: object, **options: object) -> None:
        _ = args
        command_options = _command_options_from_raw_options(self, options)
        profiling_config = command_profiling_config_from_options(options)

        if not nginx_offload_enabled():
            raise CommandError(
                "SERVE_WITH_NGINX must be enabled to profile NGINX handoff streaming."
            )

        targets = _select_streaming_targets(command_options)
        imported_videos = _selected_imported_videos(
            video_ids=command_options.video_ids,
            limit=command_options.limit,
        )
        if targets.empty and not imported_videos:
            raise CommandError(
                "No eligible processed streaming targets or imported processed "
                "videos found for "
                f"endpoint={command_options.endpoint}."
            )

        lease_count_before = MediaOperationLease.objects.count()
        started_at = time.perf_counter()
        with transaction.atomic():
            user = _resolve_request_user(command_options.user_id)
            result = run_with_optional_profile(
                lambda: _run_streaming_profile(
                    targets=targets,
                    imported_videos=imported_videos,
                    options=command_options,
                    user=user,
                ),
                config=profiling_config,
            )
            transaction.set_rollback(True)
        elapsed_seconds = time.perf_counter() - started_at

        lease_count_after = MediaOperationLease.objects.count()
        payload: dict[str, object] = {
            "endpoint": command_options.endpoint,
            "iterations": command_options.iterations,
            "rolled_back": True,
            "nginx_offload_enabled": True,
            "nginx_protected_media_url": get_protected_media_url(),
            "selected": {
                "hls": len(targets.hls),
                "mp4": len(targets.mp4),
                "frontend_videos": len(imported_videos),
            },
            "elapsed_wall_seconds": round(elapsed_seconds, 6),
            "media_operation_leases_before": lease_count_before,
            "media_operation_leases_after": lease_count_after,
            **result,
            **profiling_metadata(profiling_config),
        }

        if command_options.json_output:
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
            return

        self.stdout.write(
            self.style.SUCCESS(
                "video streaming profiling complete: "
                f"endpoint={payload['endpoint']} "
                f"iterations={payload['iterations']} "
                f"hls={len(targets.hls)} mp4={len(targets.mp4)} "
                f"requests={payload['request_count']} "
                f"elapsed_wall_seconds={payload['elapsed_wall_seconds']}"
            )
        )


def _command_options_from_raw_options(
    command: Command,
    options: dict[str, object],
) -> _CommandOptions:
    return _CommandOptions(
        endpoint=_endpoint_option(options.get("endpoint")),
        video_ids=command.selected_video_ids_from_options(options),
        limit=command.positive_limit_from_options(options),
        iterations=_positive_int_option(options.get("iterations"), "--iterations"),
        user_id=_optional_int_option(options.get("user_id"), "--user-id"),
        origin=_optional_str(options.get("origin")),
        frontend_hls_support=_frontend_hls_support_option(
            options.get("frontend_hls_support")
        ),
        json_output=bool(options.get("json_output")),
    )


def _select_streaming_targets(options: _CommandOptions) -> _StreamingTargets:
    hls_targets: tuple[_HlsStreamingTarget, ...] = ()
    mp4_targets: tuple[_Mp4StreamingTarget, ...] = ()

    if options.profiles_hls:
        hls_targets = tuple(
            _hls_target_from_artifact(artifact)
            for artifact in _selected_hls_artifacts(
                video_ids=options.video_ids,
                limit=options.limit,
            )
        )

    if options.profiles_mp4:
        mp4_targets = tuple(
            _mp4_target_from_video(video)
            for video in _selected_mp4_videos(
                video_ids=options.video_ids,
                limit=options.limit,
            )
        )

    return _StreamingTargets(hls=hls_targets, mp4=mp4_targets)


def _selected_hls_artifacts(
    *,
    video_ids: list[int] | None,
    limit: int | None,
) -> list[VideoHlsArtifact]:
    queryset = (
        VideoHlsArtifact.objects.select_related("video")
        .filter(
            artifact_kind=VideoHlsArtifact.ArtifactKind.PROCESSED.value,
            status=VideoHlsArtifact.Status.READY.value,
        )
        .order_by("video_id", "pk")
    )
    if video_ids:
        queryset = queryset.filter(video_id__in=video_ids)
    if limit is not None:
        queryset = queryset[:limit]
    return list(queryset)


def _selected_mp4_videos(
    *,
    video_ids: list[int] | None,
    limit: int | None,
) -> list[VideoFile]:
    queryset = (
        VideoFile.objects.filter(storage_mode=VideoStorageMode.STREAMABLE.value)
        .exclude(processed_streamable_relative_path="")
        .order_by("pk")
    )
    if video_ids:
        queryset = queryset.filter(pk__in=video_ids)
    if limit is not None:
        queryset = queryset[:limit]
    return list(queryset)


def _selected_imported_videos(
    *,
    video_ids: list[int] | None,
    limit: int | None,
) -> list[VideoFile]:
    queryset = (
        VideoFile.objects.exclude(processed_file="")
        .exclude(processed_file__isnull=True)
        .order_by("pk")
    )
    if video_ids:
        queryset = queryset.filter(pk__in=video_ids)
    if limit is not None:
        queryset = queryset[:limit]
    return list(queryset)


def _hls_target_from_artifact(artifact: VideoHlsArtifact) -> _HlsStreamingTarget:
    artifact_fields = cast(_HlsArtifactStreamingFields, artifact)
    try:
        hls_playlist_path(artifact)
    except (FileNotFoundError, ValueError) as exc:
        raise CommandError(
            "Ready HLS artifact is not streamable: "
            f"video_id={artifact.video_id} detail={exc}"
        ) from exc

    segment_name = _first_hls_segment_name(artifact)
    return _HlsStreamingTarget(
        video_id=int(artifact.video_id),
        key_id=artifact_fields.key_id,
        segment_name=segment_name,
    )


def _first_hls_segment_name(artifact: VideoHlsArtifact) -> str:
    artifact_fields = cast(_HlsArtifactStreamingFields, artifact)
    segment_dir = resolve_existing_protected_media_path(
        artifact_fields.segment_directory_relative_path
    )
    if segment_dir is None or not segment_dir.is_dir():
        raise CommandError(
            "Ready HLS artifact segment directory is missing: "
            f"video_id={artifact.video_id}"
        )
    for segment_path in sorted(segment_dir.glob("seg_*.ts")):
        if segment_path.is_file() and segment_path.stat().st_size > 0:
            return segment_path.name
    raise CommandError(
        f"Ready HLS artifact has no non-empty TS segments: video_id={artifact.video_id}"
    )


def _mp4_target_from_video(video: VideoFile) -> _Mp4StreamingTarget:
    relative_path = get_video_stream_relative_path(
        video,
        VideoArtifactKind.PROCESSED,
    )
    if relative_path is None:
        raise CommandError(
            f"Video {video.pk} has no processed streamable relative path."
        )
    path = resolve_existing_protected_media_path(relative_path)
    if path is None or not path.is_file() or path.stat().st_size <= 0:
        raise CommandError(
            f"Video {video.pk} processed streamable artifact is missing or empty."
        )
    return _Mp4StreamingTarget(video_id=int(video.pk), relative_path=relative_path)


def _run_streaming_profile(
    *,
    targets: _StreamingTargets,
    imported_videos: list[VideoFile],
    options: _CommandOptions,
    user: User,
) -> dict[str, object]:
    factory = APIRequestFactory()
    hls_result = _profile_hls_targets(
        factory=factory,
        targets=targets.hls,
        iterations=options.iterations,
        user=user,
        origin=options.origin,
    )
    mp4_result = _profile_mp4_targets(
        factory=factory,
        targets=targets.mp4,
        iterations=options.iterations,
        user=user,
        origin=options.origin,
    )
    frontend_result = _profile_frontend_client(
        factory=factory,
        videos=imported_videos,
        user=user,
        origin=options.origin,
        hls_support=options.frontend_hls_support,
    )
    hls_request_count = cast(int, hls_result["request_count"])
    mp4_request_count = cast(int, mp4_result["request_count"])
    frontend_request_count = cast(int, frontend_result["request_count"])
    return {
        "request_count": hls_request_count + mp4_request_count + frontend_request_count,
        "hls": hls_result,
        "mp4": mp4_result,
        "frontend_client": frontend_result,
    }


def _profile_frontend_client(
    *,
    factory: APIRequestFactory,
    videos: list[VideoFile],
    user: User,
    origin: str | None,
    hls_support: _FrontendHlsSupport,
) -> dict[str, object]:
    playlist_view = cast(Callable[..., HttpResponseBase], HLSPlaylistView.as_view())
    key_view = cast(Callable[..., HttpResponseBase], HLSKeyView.as_view())
    segment_view = cast(Callable[..., HttpResponseBase], HLSSegmentView.as_view())
    stream_view = cast(Callable[..., HttpResponseBase], VideoStreamView.as_view())
    artifacts_by_video_id = _ready_processed_hls_artifacts_for_videos(videos)

    request_count = 0
    video_results: list[dict[str, object]] = []
    playback_modes: dict[str, int] = {mode: 0 for mode in FRONTEND_PLAYBACK_MODES}
    streaming_video_present_count = 0
    nginx_handoff_ready_count = 0
    missing_resolution_count = 0
    streaming_usable_count = 0

    for video in videos:
        result, video_request_count = _simulate_frontend_video(
            factory=factory,
            playlist_view=playlist_view,
            key_view=key_view,
            segment_view=segment_view,
            stream_view=stream_view,
            video=video,
            hls_artifact=artifacts_by_video_id.get(int(video.pk)),
            user=user,
            origin=origin,
            hls_support=hls_support,
        )
        request_count += video_request_count
        video_results.append(result)
        playback_mode = str(result["playback_mode"])
        playback_modes[playback_mode] = playback_modes.get(playback_mode, 0) + 1
        if bool(result["streaming_video_present"]):
            streaming_video_present_count += 1
        if bool(result["nginx_handoff_can_work"]):
            nginx_handoff_ready_count += 1
        if not bool(result["resolution_known"]):
            missing_resolution_count += 1
        if bool(result["streaming_usable"]):
            streaming_usable_count += 1

    total_videos = len(videos)
    return {
        "request_count": request_count,
        "simulated_hls_support": hls_support,
        "total_imported_processed_videos": total_videos,
        "streaming_video_present_count": streaming_video_present_count,
        "missing_streaming_video_count": total_videos - streaming_video_present_count,
        "streaming_usable_count": streaming_usable_count,
        "nginx_handoff_ready_count": nginx_handoff_ready_count,
        "missing_resolution_count": missing_resolution_count,
        "playback_modes": playback_modes,
        "videos": video_results,
    }


def _ready_processed_hls_artifacts_for_videos(
    videos: list[VideoFile],
) -> dict[int, VideoHlsArtifact]:
    video_ids = [int(video.pk) for video in videos]
    if not video_ids:
        return {}
    artifacts = VideoHlsArtifact.objects.filter(
        video_id__in=video_ids,
        artifact_kind=VideoHlsArtifact.ArtifactKind.PROCESSED.value,
        status=VideoHlsArtifact.Status.READY.value,
    ).order_by("video_id", "pk")
    by_video_id: dict[int, VideoHlsArtifact] = {}
    for artifact in artifacts:
        by_video_id.setdefault(int(artifact.video_id), artifact)
    return by_video_id


def _simulate_frontend_video(
    *,
    factory: APIRequestFactory,
    playlist_view: Callable[..., HttpResponseBase],
    key_view: Callable[..., HttpResponseBase],
    segment_view: Callable[..., HttpResponseBase],
    stream_view: Callable[..., HttpResponseBase],
    video: VideoFile,
    hls_artifact: VideoHlsArtifact | None,
    user: User,
    origin: str | None,
    hls_support: _FrontendHlsSupport,
) -> tuple[dict[str, object], int]:
    video_id = int(video.pk)
    issues: list[str] = []
    width = _positive_dimension(getattr(video, "width", None))
    height = _positive_dimension(getattr(video, "height", None))
    resolution_known = width is not None and height is not None
    if not resolution_known:
        issues.append("missing_resolution")

    hls_probe = _inspect_hls_artifact_files(hls_artifact)
    progressive_probe = _inspect_progressive_streamable_file(video)
    streaming_video_present = bool(
        hls_probe["hls_files_present"] or progressive_probe["streamable_file_present"]
    )
    if not streaming_video_present:
        issues.append("missing_streaming_video")

    base_result: dict[str, object] = {
        "video_id": video_id,
        "resolution": f"{width}x{height}" if resolution_known else None,
        "width": width,
        "height": height,
        "resolution_known": resolution_known,
        "hls_playlist_url": _frontend_hls_playlist_url(video_id),
        "fallback_stream_url": None,
        "hls_artifact_ready": hls_artifact is not None,
        "streaming_video_present": streaming_video_present,
        "progressive_streamable_present": progressive_probe["streamable_file_present"],
        "progressive_streamable_relative_path": progressive_probe["relative_path"],
        "progressive_streamable_file_size": progressive_probe["file_size"],
        "nginx_handoff_can_work": False,
        "streaming_usable": False,
        "issues": issues,
        **hls_probe,
    }

    if hls_support == "none":
        return _simulate_frontend_progressive_fallback(
            factory=factory,
            stream_view=stream_view,
            video_id=video_id,
            user=user,
            origin=origin,
            base_result=base_result,
            request_count=0,
            fallback_reason="hls_not_supported",
        )

    playlist_response = playlist_view(
        _authenticated_get(
            factory,
            _frontend_hls_playlist_url(video_id),
            user=user,
            origin=origin,
            accept=HLS_PLAYLIST_ACCEPT,
        ),
        pk=video_id,
    )
    request_count = 1
    playlist_status = int(playlist_response.status_code)
    base_result["hls_playlist_status"] = playlist_status

    if playlist_status == 404:
        return _simulate_frontend_progressive_fallback(
            factory=factory,
            stream_view=stream_view,
            video_id=video_id,
            user=user,
            origin=origin,
            base_result=base_result,
            request_count=request_count,
            fallback_reason="hls_playlist_404",
        )

    if playlist_status != 200:
        issues.append("hls_playlist_unavailable")
        base_result["playback_mode"] = "error"
        base_result["hls_playlist_x_accel_redirect"] = _optional_x_accel_redirect(
            playlist_response
        )
        base_result["hls_playlist_handoff_ok"] = False
        return base_result, request_count

    playback_mode = _frontend_hls_playback_mode(hls_support)
    playlist_redirect = _optional_x_accel_redirect(playlist_response)
    playlist_handoff_ok = _x_accel_handoff_ok(playlist_response)
    base_result["playback_mode"] = playback_mode
    base_result["hls_playlist_x_accel_redirect"] = playlist_redirect
    base_result["hls_playlist_handoff_ok"] = playlist_handoff_ok
    if not playlist_handoff_ok:
        issues.append("hls_playlist_handoff_missing")

    key_id = hls_probe["hls_key_id"]
    segment_name = hls_probe["hls_segment_name"]
    key_ok = False
    segment_handoff_ok = False

    if isinstance(key_id, str):
        key_response = key_view(
            _authenticated_get(
                factory,
                f"/endoreg-api/media/videos/{video_id}/hls/key/{key_id}/",
                user=user,
                origin=origin,
            ),
            pk=video_id,
            key_id=UUID(key_id),
        )
        request_count += 1
        key_content_length = _content_length_header(key_response)
        key_ok = (
            key_response.status_code == 200
            and key_content_length == HLS_CONTENT_KEY_BYTES
        )
        base_result["hls_key_status"] = int(key_response.status_code)
        base_result["hls_key_content_length"] = key_content_length
        base_result["hls_key_ok"] = key_ok
        if not key_ok:
            issues.append("hls_key_unavailable")
    else:
        issues.append("hls_key_missing")

    if isinstance(key_id, str) and isinstance(segment_name, str):
        segment_response = segment_view(
            _authenticated_get(
                factory,
                "/endoreg-api/media/videos/"
                f"{video_id}/hls/segments/{key_id}/{segment_name}",
                user=user,
                origin=origin,
            ),
            pk=video_id,
            key_id=UUID(key_id),
            segment_name=segment_name,
        )
        request_count += 1
        segment_handoff_ok = _x_accel_handoff_ok(segment_response)
        base_result["hls_segment_status"] = int(segment_response.status_code)
        base_result["hls_segment_x_accel_redirect"] = _optional_x_accel_redirect(
            segment_response
        )
        base_result["hls_segment_handoff_ok"] = segment_handoff_ok
        if not segment_handoff_ok:
            issues.append("hls_segment_handoff_missing")
    else:
        issues.append("hls_segment_missing")

    hls_usable = playlist_handoff_ok and key_ok and segment_handoff_ok
    base_result["nginx_handoff_can_work"] = hls_usable
    base_result["streaming_usable"] = hls_usable
    if not hls_usable:
        issues.append("hls_streaming_unusable")
    return base_result, request_count


def _simulate_frontend_progressive_fallback(
    *,
    factory: APIRequestFactory,
    stream_view: Callable[..., HttpResponseBase],
    video_id: int,
    user: User,
    origin: str | None,
    base_result: dict[str, object],
    request_count: int,
    fallback_reason: str,
) -> tuple[dict[str, object], int]:
    response = stream_view(
        _authenticated_get(
            factory,
            _frontend_fallback_stream_url(video_id),
            user=user,
            origin=origin,
        ),
        pk=video_id,
    )
    request_count += 1
    response_status = int(response.status_code)
    handoff_ok = _x_accel_handoff_ok(response)
    issues = cast(list[str], base_result["issues"])
    if response_status == 302:
        issues.append("progressive_stream_redirect_to_hls")
    elif response_status == 410:
        issues.append("progressive_stream_gone")
    else:
        if not handoff_ok:
            issues.append("progressive_stream_handoff_missing")
        if response_status != 200:
            issues.append("progressive_stream_unavailable")

    base_result["playback_mode"] = "progressive" if response_status == 200 else "error"
    base_result["fallback_reason"] = fallback_reason
    base_result["progressive_stream_status"] = response_status
    base_result["progressive_stream_state"] = response.headers.get("X-Stream-State")
    base_result["progressive_stream_location"] = response.headers.get("Location")
    base_result["progressive_x_accel_redirect"] = _optional_x_accel_redirect(response)
    base_result["progressive_handoff_ok"] = handoff_ok
    base_result["nginx_handoff_can_work"] = handoff_ok
    base_result["streaming_usable"] = response_status == 200 and handoff_ok
    return base_result, request_count


def _inspect_hls_artifact_files(
    artifact: VideoHlsArtifact | None,
) -> dict[str, object]:
    if artifact is None:
        return {
            "hls_key_id": None,
            "hls_segment_name": None,
            "hls_playlist_file_present": False,
            "hls_segment_file_present": False,
            "hls_files_present": False,
        }

    playlist_file_present = True
    try:
        hls_playlist_path(artifact)
    except (FileNotFoundError, ValueError):
        playlist_file_present = False

    segment_name = _first_hls_segment_name_or_none(artifact)
    segment_file_present = segment_name is not None
    artifact_fields = cast(_HlsArtifactStreamingFields, artifact)
    return {
        "hls_key_id": str(artifact_fields.key_id),
        "hls_segment_name": segment_name,
        "hls_playlist_file_present": playlist_file_present,
        "hls_segment_file_present": segment_file_present,
        "hls_files_present": playlist_file_present and segment_file_present,
    }


def _first_hls_segment_name_or_none(artifact: VideoHlsArtifact) -> str | None:
    artifact_fields = cast(_HlsArtifactStreamingFields, artifact)
    segment_dir = resolve_existing_protected_media_path(
        artifact_fields.segment_directory_relative_path
    )
    if segment_dir is None or not segment_dir.is_dir():
        return None
    for segment_path in sorted(segment_dir.glob("seg_*.ts")):
        if segment_path.is_file() and segment_path.stat().st_size > 0:
            return segment_path.name
    return None


def _inspect_progressive_streamable_file(video: VideoFile) -> dict[str, object]:
    relative_path = get_video_stream_relative_path(
        video,
        VideoArtifactKind.PROCESSED,
    )
    storage_mode = cast(str | None, getattr(video, "storage_mode", None))
    if storage_mode != VideoStorageMode.STREAMABLE.value or relative_path is None:
        return {
            "relative_path": relative_path,
            "streamable_file_present": False,
            "file_size": None,
        }

    path = resolve_existing_protected_media_path(relative_path)
    if path is None or not path.is_file():
        return {
            "relative_path": relative_path,
            "streamable_file_present": False,
            "file_size": None,
        }

    file_size = path.stat().st_size
    return {
        "relative_path": relative_path,
        "streamable_file_present": file_size > 0,
        "file_size": file_size,
    }


def _profile_hls_targets(
    *,
    factory: APIRequestFactory,
    targets: tuple[_HlsStreamingTarget, ...],
    iterations: int,
    user: User,
    origin: str | None,
) -> dict[str, object]:
    playlist_view = cast(Callable[..., HttpResponseBase], HLSPlaylistView.as_view())
    key_view = cast(Callable[..., HttpResponseBase], HLSKeyView.as_view())
    segment_view = cast(Callable[..., HttpResponseBase], HLSSegmentView.as_view())
    samples: list[dict[str, object]] = []
    request_count = 0

    for iteration in range(iterations):
        for target in targets:
            sample = _exercise_hls_target(
                factory=factory,
                playlist_view=playlist_view,
                key_view=key_view,
                segment_view=segment_view,
                target=target,
                user=user,
                origin=origin,
            )
            request_count += 3
            if iteration == 0:
                samples.append(sample)

    return {
        "request_count": request_count,
        "targets": samples,
    }


def _profile_mp4_targets(
    *,
    factory: APIRequestFactory,
    targets: tuple[_Mp4StreamingTarget, ...],
    iterations: int,
    user: User,
    origin: str | None,
) -> dict[str, object]:
    stream_view = cast(Callable[..., HttpResponseBase], VideoStreamView.as_view())
    samples: list[dict[str, object]] = []
    request_count = 0

    for iteration in range(iterations):
        for target in targets:
            sample = _exercise_mp4_target(
                factory=factory,
                stream_view=stream_view,
                target=target,
                user=user,
                origin=origin,
            )
            request_count += 1
            if iteration == 0:
                samples.append(sample)

    return {
        "request_count": request_count,
        "targets": samples,
    }


def _exercise_hls_target(
    *,
    factory: APIRequestFactory,
    playlist_view: Callable[..., HttpResponseBase],
    key_view: Callable[..., HttpResponseBase],
    segment_view: Callable[..., HttpResponseBase],
    target: _HlsStreamingTarget,
    user: User,
    origin: str | None,
) -> dict[str, object]:
    # These direct APIRequestFactory calls don't run through Django's request
    # handler. Calling HttpResponse.close() here emits request_finished, which
    # can close the active DB connection inside the rollback transaction.
    playlist_response = playlist_view(
        _authenticated_get(
            factory,
            f"/endoreg-api/media/videos/{target.video_id}/hls/playlist/",
            user=user,
            origin=origin,
        ),
        pk=target.video_id,
    )
    _assert_status_ok(playlist_response, label="hls_playlist")
    playlist_redirect = _required_x_accel_redirect(
        playlist_response,
        label="hls_playlist",
    )

    key_response = key_view(
        _authenticated_get(
            factory,
            f"/endoreg-api/media/videos/{target.video_id}/hls/key/{target.key_id}/",
            user=user,
            origin=origin,
        ),
        pk=target.video_id,
        key_id=target.key_id,
    )
    _assert_status_ok(key_response, label="hls_key")
    key_content_length = _content_length_header(key_response)

    segment_response = segment_view(
        _authenticated_get(
            factory,
            "/endoreg-api/media/videos/"
            f"{target.video_id}/hls/segments/{target.key_id}/{target.segment_name}",
            user=user,
            origin=origin,
        ),
        pk=target.video_id,
        key_id=target.key_id,
        segment_name=target.segment_name,
    )
    _assert_status_ok(segment_response, label="hls_segment")
    segment_redirect = _required_x_accel_redirect(
        segment_response,
        label="hls_segment",
    )

    return {
        "video_id": target.video_id,
        "key_id": str(target.key_id),
        "segment_name": target.segment_name,
        "playlist_x_accel_redirect": playlist_redirect,
        "segment_x_accel_redirect": segment_redirect,
        "key_content_length": key_content_length,
    }


def _exercise_mp4_target(
    *,
    factory: APIRequestFactory,
    stream_view: Callable[..., HttpResponseBase],
    target: _Mp4StreamingTarget,
    user: User,
    origin: str | None,
) -> dict[str, object]:
    response = stream_view(
        _authenticated_get(
            factory,
            f"/endoreg-api/media/videos/{target.video_id}/stream/?type=processed",
            user=user,
            origin=origin,
        ),
        pk=target.video_id,
    )
    response_status = int(response.status_code)
    if response_status != 302:
        raise CommandError(
            "mp4_stream expected HLS compatibility redirect status 302, "
            f"got {response_status}"
        )

    return {
        "video_id": target.video_id,
        "relative_path": target.relative_path,
        "legacy_stream_status": response_status,
        "legacy_stream_state": response.headers.get("X-Stream-State"),
        "legacy_stream_location": response.headers.get("Location"),
        "x_accel_redirect": _optional_x_accel_redirect(response),
        "lease_token_present": bool(response.headers.get("X-Media-Operation-Lease")),
    }


def _authenticated_get(
    factory: APIRequestFactory,
    path: str,
    *,
    user: User,
    origin: str | None,
    accept: str | None = None,
) -> WSGIRequest:
    if origin is not None and accept is not None:
        request = cast(
            WSGIRequest,
            factory.get(path, HTTP_ORIGIN=origin, HTTP_ACCEPT=accept),
        )
    elif origin is not None:
        request = cast(WSGIRequest, factory.get(path, HTTP_ORIGIN=origin))
    elif accept is not None:
        request = cast(WSGIRequest, factory.get(path, HTTP_ACCEPT=accept))
    else:
        request = cast(WSGIRequest, factory.get(path))
    force_authenticate(cast(Any, request), user=user)
    return request


def _assert_status_ok(response: HttpResponseBase, *, label: str) -> None:
    if response.status_code != 200:
        raise CommandError(
            f"{label} returned status={response.status_code}; expected 200."
        )


def _required_x_accel_redirect(response: HttpResponseBase, *, label: str) -> str:
    redirect = response.headers.get("X-Accel-Redirect", "")
    if not redirect:
        raise CommandError(f"{label} did not return X-Accel-Redirect.")
    protected_prefix = get_protected_media_url().rstrip("/") + "/"
    if not redirect.startswith(protected_prefix):
        raise CommandError(
            f"{label} returned unexpected X-Accel-Redirect={redirect!r}; "
            f"expected prefix {protected_prefix!r}."
        )
    return redirect


def _optional_x_accel_redirect(response: HttpResponseBase) -> str | None:
    redirect = response.headers.get("X-Accel-Redirect")
    if redirect is None or redirect == "":
        return None
    return redirect


def _x_accel_handoff_ok(response: HttpResponseBase) -> bool:
    redirect = _optional_x_accel_redirect(response)
    if redirect is None:
        return False
    protected_prefix = get_protected_media_url().rstrip("/") + "/"
    return redirect.startswith(protected_prefix)


def _content_length_header(response: HttpResponseBase) -> int | None:
    value = response.headers.get("Content-Length")
    if value is None:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise CommandError(f"Invalid Content-Length header: {value!r}") from exc


def _resolve_request_user(user_id: int | None) -> User:
    if user_id is not None:
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist as exc:
            raise CommandError(f"User not found: {user_id}") from exc

    username = f"profile-video-streaming-{os.getpid()}"
    user = User.objects.create_user(username=username)
    group, _created = Group.objects.get_or_create(name="endoregdb_user")
    cast(_UserWithGroups, user).groups.add(group)
    return user


def _endpoint_option(value: object) -> _Endpoint:
    text = str(value or "hls").strip().lower()
    if text in {"hls", "mp4", "all"}:
        return cast(_Endpoint, text)
    raise CommandError("--endpoint must be one of: hls, mp4, all")


def _frontend_hls_support_option(value: object) -> _FrontendHlsSupport:
    text = str(value or "hlsjs").strip().lower()
    if text in {"hlsjs", "native", "none"}:
        return cast(_FrontendHlsSupport, text)
    raise CommandError("--frontend-hls-support must be one of: hlsjs, native, none")


def _frontend_hls_playback_mode(
    hls_support: _FrontendHlsSupport,
) -> _FrontendPlaybackMode:
    if hls_support == "hlsjs":
        return "hls"
    if hls_support == "native":
        return "native_hls"
    raise CommandError("HLS playback mode requested for a client without HLS support.")


def _positive_dimension(value: object) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    return None


def _frontend_hls_playlist_url(video_id: int) -> str:
    return f"/endoreg-api/media/videos/{video_id}/hls/playlist.m3u8?type=processed"


def _frontend_fallback_stream_url(video_id: int) -> str:
    return f"/endoreg-api/media/videos/{video_id}/stream/?type=processed"


def _positive_int_option(value: object, label: str) -> int:
    try:
        result = int(str(value))
    except (TypeError, ValueError) as exc:
        raise CommandError(f"{label} must be a positive integer.") from exc
    if result <= 0:
        raise CommandError(f"{label} must be a positive integer.")
    return result


def _optional_int_option(value: object, label: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError) as exc:
        raise CommandError(f"{label} must be an integer.") from exc


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

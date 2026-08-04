from __future__ import annotations

from typing import Any, Protocol, cast

import pytest
from django.contrib.auth.models import Group, User
from django.core.files.base import ContentFile
from django.test import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from endoreg_db.models import Center, Examiner, PortalUserInfo, VideoFile
from endoreg_db.models.media.video.hls_artifact import VideoHlsArtifact
from endoreg_db.models.media.operation_lease import MediaOperationLease
from endoreg_db.services import hls_media
from endoreg_db.views import access_control
from endoreg_db.views.video import hls_stream
from tests.helpers.hls import FakeHlsOutputRecorder

pytestmark = pytest.mark.django_db


class _GroupRelation(Protocol):
    def add(self, *groups: Group) -> None: ...


class _UserWithGroups(Protocol):
    groups: _GroupRelation


@pytest.fixture
def hls_view_center() -> Center:
    return Center.objects.create(
        name="hls-view-center",
        display_name="HLS View Center",
    )


def _create_processed_video(center: Center) -> VideoFile:
    video = VideoFile.objects.create(
        center=center,
        video_hash="hls-view-video",
    )
    cast(Any, video.processed_file).save(
        "view-source.mp4",
        ContentFile(b"view source payload"),
        save=True,
    )
    state = video.get_or_create_state()
    state.anonymized = True
    state.processing_error = False
    state.save(update_fields=["anonymized", "processing_error"])
    return video


@pytest.fixture
def hls_artifact(
    hls_view_center: Center,
    monkeypatch: pytest.MonkeyPatch,
) -> VideoHlsArtifact:
    video = _create_processed_video(hls_view_center)
    fake_hls = FakeHlsOutputRecorder(include_version_tag=False)
    monkeypatch.setattr(hls_media, "_run_ffmpeg_hls", fake_hls.run)
    hls_media.materialize_video_hls(video.pk, artifact_kind="processed")
    return VideoHlsArtifact.objects.get(video=video, artifact_kind="processed")


def _authenticated_request(
    path: str,
    user: User,
    *,
    origin: str | None = None,
    global_access: bool = True,
) -> Any:
    # Staff users are intentionally cross-center operators in the production
    # access-control contract. Center-scoped denial is covered separately.
    user.is_staff = global_access
    factory = APIRequestFactory()
    if origin is None:
        request = factory.get(path)
    else:
        request = factory.get(path, HTTP_ORIGIN=origin)
    force_authenticate(cast(Any, request), user=user)
    return request


def _artifact_video_pk(hls_artifact: VideoHlsArtifact) -> int:
    hls_video = cast(Any, hls_artifact).video
    return hls_video.pk


def _assert_no_cors_headers(response: Any) -> None:
    assert "Access-Control-Allow-Origin" not in response.headers
    assert "Access-Control-Allow-Credentials" not in response.headers


def _assert_explicit_credentialed_cors(response: Any, origin: str) -> None:
    assert response["Access-Control-Allow-Origin"] == origin
    assert response["Access-Control-Allow-Origin"] != "*"
    assert response["Access-Control-Allow-Credentials"] == "true"


def test_hls_playlist_and_segment_use_nginx_accel_after_authz(
    hls_artifact: VideoHlsArtifact,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DJANGO_CORS_ALLOWED_ORIGINS", raising=False)
    monkeypatch.setenv("SERVE_WITH_NGINX", "true")
    monkeypatch.setenv("NGINX_PROTECTED_MEDIA_URL", "/protected_media/")
    user = User.objects.create_user(username="hls-reader")
    video_pk = _artifact_video_pk(hls_artifact)

    playlist_response = hls_stream.HLSPlaylistView.as_view()(
        _authenticated_request(
            f"/endoreg-api/media/videos/{video_pk}/hls/playlist/",
            user,
        ),
        pk=video_pk,
    )
    assert playlist_response.status_code == 200
    assert cast(Any, playlist_response).content == b""
    assert (
        playlist_response["X-Accel-Redirect"]
        == f"/protected_media/{hls_artifact.playlist_relative_path}"
    )
    assert playlist_response["Cache-Control"] == "no-store, private"
    assert playlist_response["X-Content-Type-Options"] == "nosniff"
    _assert_no_cors_headers(playlist_response)

    segment_response = hls_stream.HLSSegmentView.as_view()(
        _authenticated_request(
            f"/endoreg-api/media/videos/{video_pk}/hls/segments/"
            f"{hls_artifact.key_id}/seg_000.ts",
            user,
        ),
        pk=video_pk,
        key_id=hls_artifact.key_id,
        segment_name="seg_000.ts",
    )
    assert segment_response.status_code == 200
    assert cast(Any, segment_response).content == b""
    assert (
        segment_response["X-Accel-Redirect"] == "/protected_media/"
        f"{hls_artifact.segment_directory_relative_path}/seg_000.ts"
    )
    assert segment_response["Cache-Control"] == hls_stream.HLS_SEGMENT_CACHE_CONTROL
    assert segment_response["X-Content-Type-Options"] == "nosniff"
    assert "public" not in segment_response["Cache-Control"]
    _assert_no_cors_headers(segment_response)
    lease_types = set(
        MediaOperationLease.objects.filter(video_id=video_pk).values_list(
            "metadata__file_type",
            flat=True,
        )
    )
    assert lease_types == {"hls_processed_playlist", "hls_processed_segment"}


def test_hls_playlist_access_renews_one_media_operation_lease(
    hls_artifact: VideoHlsArtifact,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SERVE_WITH_NGINX", "true")
    monkeypatch.setenv("NGINX_PROTECTED_MEDIA_URL", "/protected_media/")
    user = User.objects.create_user(username="hls-lease-renewal-reader")
    video_pk = _artifact_video_pk(hls_artifact)
    view = hls_stream.HLSPlaylistView.as_view()

    first = view(
        _authenticated_request(
            f"/endoreg-api/media/videos/{video_pk}/hls/playlist/",
            user,
        ),
        pk=video_pk,
    )
    first_lease = MediaOperationLease.objects.get(
        video_id=video_pk,
        metadata__file_type="hls_processed_playlist",
    )
    first_expiry = first_lease.expires_at
    second = view(
        _authenticated_request(
            f"/endoreg-api/media/videos/{video_pk}/hls/playlist/",
            user,
        ),
        pk=video_pk,
    )

    first_lease.refresh_from_db()
    assert first.status_code == 200
    assert second.status_code == 200
    assert first_lease.expires_at >= first_expiry
    assert (
        MediaOperationLease.objects.filter(
            video_id=video_pk,
            metadata__file_type="hls_processed_playlist",
        ).count()
        == 1
    )


def test_hls_playlist_and_key_serve_same_origin_without_cors_headers(
    hls_artifact: VideoHlsArtifact,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DJANGO_CORS_ALLOWED_ORIGINS", raising=False)
    monkeypatch.delenv("SERVE_WITH_NGINX", raising=False)
    user = User.objects.create_user(username="hls-same-origin-reader")
    video_pk = _artifact_video_pk(hls_artifact)

    playlist_response = hls_stream.HLSPlaylistView.as_view()(
        _authenticated_request(
            f"/endoreg-api/media/videos/{video_pk}/hls/playlist/",
            user,
        ),
        pk=video_pk,
    )
    assert playlist_response.status_code == 200
    assert playlist_response["Content-Type"] == "application/vnd.apple.mpegurl"
    assert playlist_response["Cache-Control"] == "no-store, private"
    assert playlist_response["X-Content-Type-Options"] == "nosniff"
    _assert_no_cors_headers(playlist_response)
    playlist_response.close()

    key_response = hls_stream.HLSKeyView.as_view()(
        _authenticated_request(
            f"/endoreg-api/media/videos/{video_pk}/hls/key/{hls_artifact.key_id}/",
            user,
        ),
        pk=video_pk,
        key_id=hls_artifact.key_id,
    )
    assert key_response.status_code == 200
    assert len(cast(Any, key_response).content) == hls_media.HLS_CONTENT_KEY_BYTES
    assert key_response["Cache-Control"] == "no-store, private"
    assert key_response["X-Content-Type-Options"] == "nosniff"
    _assert_no_cors_headers(key_response)


def test_hls_segment_fails_closed_without_nginx_offload(
    hls_artifact: VideoHlsArtifact,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DJANGO_CORS_ALLOWED_ORIGINS", raising=False)
    monkeypatch.delenv("SERVE_WITH_NGINX", raising=False)
    user = User.objects.create_user(username="hls-no-nginx-reader")
    video_pk = _artifact_video_pk(hls_artifact)

    segment_response = hls_stream.HLSSegmentView.as_view()(
        _authenticated_request(
            f"/endoreg-api/media/videos/{video_pk}/hls/segments/"
            f"{hls_artifact.key_id}/seg_000.ts",
            user,
        ),
        pk=video_pk,
        key_id=hls_artifact.key_id,
        segment_name="seg_000.ts",
    )
    assert segment_response.status_code == 404
    assert "X-Accel-Redirect" not in segment_response.headers
    assert segment_response["Content-Type"] != "video/mp2t"
    _assert_no_cors_headers(segment_response)


def test_hls_views_preserve_configured_cross_origin_cors(
    hls_artifact: VideoHlsArtifact,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frontend_origin = "http://frontend.test"
    monkeypatch.setenv("DJANGO_CORS_ALLOWED_ORIGINS", frontend_origin)
    monkeypatch.setenv("SERVE_WITH_NGINX", "true")
    monkeypatch.setenv("NGINX_PROTECTED_MEDIA_URL", "/protected_media/")
    user = User.objects.create_user(username="hls-cross-origin-reader")
    video_pk = _artifact_video_pk(hls_artifact)

    playlist_response = hls_stream.HLSPlaylistView.as_view()(
        _authenticated_request(
            f"/endoreg-api/media/videos/{video_pk}/hls/playlist/",
            user,
            origin=frontend_origin,
        ),
        pk=video_pk,
    )
    assert playlist_response.status_code == 200
    _assert_explicit_credentialed_cors(playlist_response, frontend_origin)
    playlist_response.close()

    key_response = hls_stream.HLSKeyView.as_view()(
        _authenticated_request(
            f"/endoreg-api/media/videos/{video_pk}/hls/key/{hls_artifact.key_id}/",
            user,
            origin=frontend_origin,
        ),
        pk=video_pk,
        key_id=hls_artifact.key_id,
    )
    assert key_response.status_code == 200
    _assert_explicit_credentialed_cors(key_response, frontend_origin)

    segment_response = hls_stream.HLSSegmentView.as_view()(
        _authenticated_request(
            f"/endoreg-api/media/videos/{video_pk}/hls/segments/"
            f"{hls_artifact.key_id}/seg_000.ts",
            user,
            origin=frontend_origin,
        ),
        pk=video_pk,
        key_id=hls_artifact.key_id,
        segment_name="seg_000.ts",
    )
    assert segment_response.status_code == 200
    assert (
        segment_response["X-Accel-Redirect"] == "/protected_media/"
        f"{hls_artifact.segment_directory_relative_path}/seg_000.ts"
    )
    assert segment_response["Cache-Control"] == hls_stream.HLS_SEGMENT_CACHE_CONTROL
    assert "public" not in segment_response["Cache-Control"]
    _assert_explicit_credentialed_cors(segment_response, frontend_origin)


def test_hls_key_view_returns_ephemeral_uncached_key(
    hls_artifact: VideoHlsArtifact,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DJANGO_CORS_ALLOWED_ORIGINS", raising=False)
    user = User.objects.create_user(username="hls-key-reader")
    video_pk = _artifact_video_pk(hls_artifact)

    response = hls_stream.HLSKeyView.as_view()(
        _authenticated_request(
            f"/endoreg-api/media/videos/{video_pk}/hls/key/{hls_artifact.key_id}/",
            user,
        ),
        pk=video_pk,
        key_id=hls_artifact.key_id,
    )

    assert response.status_code == 200
    assert len(cast(Any, response).content) == hls_media.HLS_CONTENT_KEY_BYTES
    assert response["Content-Type"] == "application/octet-stream"
    assert response["Cache-Control"] == "no-store, private"
    assert response["Pragma"] == "no-cache"
    assert response["Expires"] == "0"
    assert response["X-Content-Type-Options"] == "nosniff"
    _assert_no_cors_headers(response)


def test_hls_playlist_rejects_raw_artifact_request(
    hls_artifact: VideoHlsArtifact,
) -> None:
    user = User.objects.create_user(username="hls-raw-reader")
    video_pk = _artifact_video_pk(hls_artifact)

    response = hls_stream.HLSPlaylistView.as_view()(
        _authenticated_request(
            f"/endoreg-api/media/videos/{video_pk}/hls/playlist/?type=raw",
            user,
        ),
        pk=video_pk,
    )

    assert response.status_code == 404


def test_center_user_can_stream_ready_raw_hls_from_assigned_center(
    hls_artifact: VideoHlsArtifact,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SERVE_WITH_NGINX", raising=False)
    video = hls_artifact.video
    video.raw_file.save(
        "raw-view-source.mp4",
        ContentFile(b"raw view source payload"),
        save=True,
    )
    fake_hls = FakeHlsOutputRecorder(include_version_tag=False)
    monkeypatch.setattr(hls_media, "_run_ffmpeg_hls", fake_hls.run)
    hls_media.materialize_video_hls(video.pk, artifact_kind="raw")

    user = User.objects.create_user(username="center-raw-reader")
    cast(_UserWithGroups, user).groups.add(Group.objects.create(name="video:read"))
    examiner = Examiner.objects.create(
        first_name="Center",
        last_name="Reader",
        center=video.center,
        hash="center-raw-reader-hash",
        is_real_person=False,
    )
    PortalUserInfo.objects.create(user=user, examiner=examiner)
    request = _authenticated_request(
        f"/endoreg-api/media/videos/{video.pk}/hls/playlist/?type=raw",
        user,
    )
    user.is_staff = False

    response = hls_stream.HLSPlaylistView.as_view()(request, pk=video.pk)

    assert response.status_code == 200
    assert response["Content-Type"] == hls_stream.HLS_PLAYLIST_CONTENT_TYPE
    response.close()


def test_hls_playlist_rejects_video_outside_user_center_scope(
    hls_artifact: VideoHlsArtifact,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = User.objects.create_user(username="hls-wrong-center-reader")
    video_pk = _artifact_video_pk(hls_artifact)
    request = _authenticated_request(
        f"/endoreg-api/media/videos/{video_pk}/hls/playlist.m3u8",
        user,
    )
    user.is_staff = False

    def resolve_other_center(_user: object) -> int:
        return int(hls_artifact.video.center_id) + 1

    monkeypatch.setattr(
        access_control,
        "resolve_allowed_center_id",
        resolve_other_center,
    )

    response = hls_stream.HLSPlaylistView.as_view()(request, pk=video_pk)

    assert response.status_code == 403


@override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
def test_centerless_hub_user_can_read_processed_playlist_key_and_segment(
    hls_artifact: VideoHlsArtifact,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SERVE_WITH_NGINX", "true")
    monkeypatch.setenv("NGINX_PROTECTED_MEDIA_URL", "/protected_media/")
    user = User.objects.create_user(username="centerless-hub-hls-reader")
    video_pk = _artifact_video_pk(hls_artifact)

    playlist = hls_stream.HLSPlaylistView.as_view()(
        _authenticated_request(
            f"/endoreg-api/media/videos/{video_pk}/hls/playlist/",
            user,
            global_access=False,
        ),
        pk=video_pk,
    )
    key = hls_stream.HLSKeyView.as_view()(
        _authenticated_request(
            f"/endoreg-api/media/videos/{video_pk}/hls/key/{hls_artifact.key_id}/",
            user,
            global_access=False,
        ),
        pk=video_pk,
        key_id=hls_artifact.key_id,
    )
    segment = hls_stream.HLSSegmentView.as_view()(
        _authenticated_request(
            f"/endoreg-api/media/videos/{video_pk}/hls/segments/"
            f"{hls_artifact.key_id}/seg_000.ts",
            user,
            global_access=False,
        ),
        pk=video_pk,
        key_id=hls_artifact.key_id,
        segment_name="seg_000.ts",
    )

    assert playlist.status_code == 200
    assert key.status_code == 200
    assert segment.status_code == 200


@override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
def test_central_hub_processed_playlist_rejects_anonymous_user(
    hls_artifact: VideoHlsArtifact,
) -> None:
    video_pk = _artifact_video_pk(hls_artifact)
    request = APIRequestFactory().get(
        f"/endoreg-api/media/videos/{video_pk}/hls/playlist/"
    )

    response = hls_stream.HLSPlaylistView.as_view()(request, pk=video_pk)

    assert response.status_code == 403


@override_settings(DEBUG=False)
def test_hls_playlist_permission_denial_happens_before_artifact_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("DJANGO_DEBUG", "false")

    def fail_if_handler_resolves_video(pk: int | str | None) -> VideoFile:
        _ = pk
        raise AssertionError("HLS handler resolved video before permission denial")

    monkeypatch.setattr(hls_stream, "_get_video_or_404", fail_if_handler_resolves_video)

    request = APIRequestFactory().get("/endoreg-api/media/videos/1/hls/playlist/")
    response = hls_stream.HLSPlaylistView.as_view()(request, pk=1)

    assert response.status_code == 403

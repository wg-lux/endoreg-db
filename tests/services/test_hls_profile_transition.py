# pyright: reportPrivateUsage=false
from __future__ import annotations

from uuid import uuid4

import pytest

from endoreg_db.models import Center, VideoFile
from endoreg_db.models.media.video.hls_artifact import VideoHlsArtifact
from endoreg_db.services import hls_media
from tests.helpers.hls import FakeHlsOutputRecorder
from tests.services.test_hls_media import _create_processed_video

pytestmark = pytest.mark.django_db
CPU = "clinical_h264_libx264_crf_v1"
GPU = "clinical_h264_nvenc_cq_v1"


@pytest.fixture
def video() -> VideoFile:
    center = Center.objects.create(name="profile-transition", display_name="Transition")
    return _create_processed_video(center=center)


@pytest.mark.parametrize("original,new_default", [(CPU, GPU), (GPU, CPU)])
@pytest.mark.parametrize("inline_claim", [False, True])
def test_reserved_profile_survives_default_change(
    video: VideoFile,
    monkeypatch: pytest.MonkeyPatch,
    original: str,
    new_default: str,
    inline_claim: bool,
) -> None:
    fake = FakeHlsOutputRecorder()
    monkeypatch.setattr(hls_media, "_run_ffmpeg_hls", fake.run)
    monkeypatch.setenv("ENDOREG_HLS_ENCODING_PROFILE", original)
    reservation = hls_media.reserve_hls_materialization_dispatch(video_id=video.pk)
    monkeypatch.setenv("ENDOREG_HLS_ENCODING_PROFILE", new_default)
    if inline_claim:
        result = hls_media.materialize_video_hls(video.pk, claim_queued=True)
    else:
        result = hls_media.materialize_video_hls(
            video.pk,
            reserved_artifact_id=reservation.artifact_id,
            reservation_key_id=reservation.attempt_key_id,
        )
    assert result.status == "materialized"
    artifact = VideoHlsArtifact.objects.get(pk=reservation.artifact_id)
    assert artifact.encoding_profile_name == original
    assert hls_media.get_ready_hls_artifact(video=video).pk == artifact.pk
    assert (
        hls_media.get_ready_hls_artifact_by_key(video=video, key_id=artifact.key_id).pk
        == artifact.pk
    )
    assert hls_media.materialize_video_hls(video.pk).status == "already_ready"
    assert (
        hls_media.reserve_hls_materialization_dispatch(video_id=video.pk).status
        == "already_ready"
    )
    if not inline_claim:
        assert (
            hls_media.materialize_video_hls(
                video.pk,
                reserved_artifact_id=reservation.artifact_id,
                reservation_key_id=reservation.attempt_key_id,
            ).status
            == "already_ready"
        )
    assert len(fake.source_payloads) == 1
    replacement = hls_media.reserve_hls_materialization_dispatch(
        video_id=video.pk, force=True
    )
    assert (
        VideoHlsArtifact.objects.get(pk=replacement.artifact_id).encoding_profile_name
        == new_default
    )


@pytest.mark.parametrize("corruption", ["profile", "source", "key"])
def test_profile_transition_retains_attempt_fencing(
    video: VideoFile,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    monkeypatch.setenv("ENDOREG_HLS_ENCODING_PROFILE", CPU)
    reservation = hls_media.reserve_hls_materialization_dispatch(video_id=video.pk)
    monkeypatch.setenv("ENDOREG_HLS_ENCODING_PROFILE", GPU)
    artifact = VideoHlsArtifact.objects.get(pk=reservation.artifact_id)
    if corruption == "profile":
        artifact.encoding_profile_name = "unsupported"
    elif corruption == "source":
        artifact.source_content_hash = "stale"
    else:
        artifact.key_id = uuid4()
    artifact.save()
    with pytest.raises((ValueError, RuntimeError)):
        hls_media.materialize_video_hls(
            video.pk,
            reserved_artifact_id=reservation.artifact_id,
            reservation_key_id=reservation.attempt_key_id,
        )


@pytest.mark.parametrize("corruption", ["profile", "source"])
def test_ready_transition_rejects_invalid_provenance(
    video: VideoFile,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    monkeypatch.setenv("ENDOREG_HLS_ENCODING_PROFILE", CPU)
    monkeypatch.setattr(hls_media, "_run_ffmpeg_hls", FakeHlsOutputRecorder().run)
    hls_media.materialize_video_hls(video.pk)
    artifact = VideoHlsArtifact.objects.get(video=video, status="ready")
    if corruption == "profile":
        artifact.encoding_profile_name = "unsupported"
    else:
        artifact.source_content_hash = "stale"
    artifact.save()
    monkeypatch.setenv("ENDOREG_HLS_ENCODING_PROFILE", GPU)
    with pytest.raises(FileNotFoundError):
        hls_media.get_ready_hls_artifact(video=video)
    with pytest.raises(FileNotFoundError):
        hls_media.get_ready_hls_artifact_by_key(video=video, key_id=artifact.key_id)

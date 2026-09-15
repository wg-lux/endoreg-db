# pyright: reportPrivateUsage=false
from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from django.core.files.base import ContentFile
from django.utils import timezone

from endoreg_db.models import Center
from endoreg_db.models.media.video.hls_artifact import VideoHlsArtifact
from endoreg_db.services import hls_media, hls_legacy_adoption
from endoreg_db.utils.paths import EndoregPathsModel
from tests.helpers.hls import FakeHlsOutputRecorder
from tests.services.test_hls_media import _create_processed_video

pytestmark = pytest.mark.django_db


@pytest.fixture
def legacy(monkeypatch: pytest.MonkeyPatch) -> VideoHlsArtifact:
    center = Center.objects.create(
        name="legacy-adoption", display_name="Legacy adoption"
    )
    video = _create_processed_video(center=center)
    monkeypatch.setattr(hls_media, "_run_ffmpeg_hls", FakeHlsOutputRecorder().run)
    hls_media.materialize_video_hls(video.pk)
    artifact = VideoHlsArtifact.objects.get(video=video, status="ready")
    artifact.source_content_hash = ""
    artifact.source_generation_id = uuid4()
    artifact.save()
    return artifact


def adopt(
    artifact: VideoHlsArtifact, *, apply: bool = True
) -> hls_legacy_adoption.AdoptionReceipt:
    return hls_legacy_adoption.adopt_legacy_hls(
        artifact_id=artifact.pk,
        approved_by="test-operator",
        reason="Explicit confirmation that historical media is unchanged",
        created_before=timezone.now() + timedelta(seconds=1),
        accept_unchanged_source=True,
        apply=apply,
    )


def test_adoption_preserves_output_and_fences_unnecessary_queue(
    legacy: VideoHlsArtifact,
) -> None:
    reservation = hls_media.reserve_hls_materialization_dispatch(
        video_id=legacy.video_id
    )
    original_key = hls_media.unwrap_hls_content_key(legacy)
    playlist = hls_media.hls_playlist_path(legacy)
    before = playlist.read_bytes()
    receipt = adopt(legacy)
    legacy.refresh_from_db()
    assert receipt.status == "committed"
    assert receipt.previous_source_content_hash == ""
    assert legacy.source_content_hash == receipt.source_content_hash
    assert legacy.key_id == receipt.key_id
    assert hls_media.unwrap_hls_content_key(legacy) == original_key
    assert playlist.read_bytes() == before
    assert hls_media.get_ready_hls_artifact(video=legacy.video).pk == legacy.pk
    assert (
        hls_media.get_ready_hls_artifact_by_key(
            video=legacy.video, key_id=legacy.key_id
        ).pk
        == legacy.pk
    )
    assert hls_media.materialize_video_hls(legacy.video_id).status == "already_ready"
    queued = VideoHlsArtifact.objects.get(pk=reservation.artifact_id)
    assert queued.status == "failed"
    assert queued.error_code == "stale_attempt"
    root = EndoregPathsModel.from_environment().protected_root / "hls-legacy-adoption"
    records = list(root.glob(f"{receipt.receipt_id}-*.json"))
    assert len(records) == 2
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in records)
    with pytest.raises(RuntimeError, match="no longer active"):
        hls_media.materialize_video_hls(
            legacy.video_id,
            reserved_artifact_id=reservation.artifact_id,
            reservation_key_id=reservation.attempt_key_id,
        )
    with pytest.raises(ValueError, match="blank historical"):
        adopt(legacy)


def test_dry_run_does_not_change_database(legacy: VideoHlsArtifact) -> None:
    receipt = adopt(legacy, apply=False)
    legacy.refresh_from_db()
    assert receipt.status == "dry_run"
    assert legacy.source_content_hash == ""
    assert legacy.source_generation_id == receipt.previous_source_generation_id


def test_future_source_change_still_invalidates_adopted_hls(
    legacy: VideoHlsArtifact,
) -> None:
    adopt(legacy)
    legacy.video.processed_file.save(
        "changed.mp4", ContentFile(b"different"), save=True
    )
    with pytest.raises(FileNotFoundError):
        hls_media.get_ready_hls_artifact(video=legacy.video)


@pytest.mark.parametrize(
    "failure",
    [
        "missing_segment",
        "bad_key",
        "nonempty_hash",
        "active_encode",
        "renamed_different_source",
    ],
)
def test_unsafe_adoption_is_rejected(legacy: VideoHlsArtifact, failure: str) -> None:
    if failure == "missing_segment":
        playlist = hls_media.hls_playlist_path(legacy)
        next(playlist.parent.glob("*.ts")).unlink()
    elif failure == "bad_key":
        legacy.key_ciphertext = b"bad authentication tag"
        legacy.save()
    elif failure == "nonempty_hash":
        legacy.source_content_hash = "a" * 64
        legacy.save()
    elif failure == "active_encode":
        reservation = hls_media.reserve_hls_materialization_dispatch(
            video_id=legacy.video_id
        )
        VideoHlsArtifact.objects.filter(pk=reservation.artifact_id).update(
            status="materializing"
        )
    else:
        legacy.video.processed_file.save(
            "replacement.mp4", ContentFile(b"different source"), save=True
        )
    from cryptography.exceptions import InvalidTag

    with pytest.raises((ValueError, RuntimeError, InvalidTag)):
        adopt(legacy)
    legacy.refresh_from_db()
    assert legacy.source_content_hash == (
        "a" * 64 if failure == "nonempty_hash" else ""
    )


def test_receipt_failure_prevents_database_rewrite(
    legacy: VideoHlsArtifact, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_receipt(receipt: hls_legacy_adoption.AdoptionReceipt) -> None:
        del receipt
        raise OSError("audit volume unavailable")

    monkeypatch.setattr(hls_legacy_adoption, "_write_receipt", fail_receipt)
    with pytest.raises(OSError):
        adopt(legacy)
    legacy.refresh_from_db()
    assert legacy.source_content_hash == ""

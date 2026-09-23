"""Processed delivery always selects the sole canonical master."""

import pytest
from django.core.files.base import ContentFile

from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.hls_media import (
    resolve_hls_source,
    resolve_hls_timeline_validation,
)
from endoreg_db.services.video_files.streaming import resolve_video_stream_source
from endoreg_db.services.video_files.types import VideoArtifactKind
from endoreg_db.utils.encryption.encrypted import LazyEncryptedStorage


def _video() -> VideoFile:
    return VideoFile(
        processed_file="processed_videos_final/master.mp4",
        processed_video_hash="a" * 64,
        raw_video_hash="c" * 64,
    )


def test_processed_stream_uses_canonical_encrypted_field() -> None:
    video = _video()
    storage = video.processed_file.storage
    name = storage.save(str(video.processed_file.name), ContentFile(b"processed media"))
    video.processed_file.name = name
    try:
        field, local_path = resolve_video_stream_source(
            video, VideoArtifactKind.PROCESSED
        )
        assert field is video.processed_file
        assert isinstance(field.storage, LazyEncryptedStorage)
        assert local_path is None
    finally:
        storage.delete(name)


def test_hls_source_and_generation_follow_canonical_master() -> None:
    video = _video()
    source = resolve_hls_source(video, VideoArtifactKind.PROCESSED)
    before = resolve_hls_timeline_validation(video, VideoArtifactKind.PROCESSED)
    assert source.field_file is video.processed_file
    assert before.expected_content_hash == "a" * 64
    video.processed_video_hash = "d" * 64
    after = resolve_hls_timeline_validation(video, VideoArtifactKind.PROCESSED)
    assert after.expected_content_hash == "d" * 64
    assert before.source_generation_id != after.source_generation_id


def test_missing_canonical_source_rejects_hls() -> None:
    video = _video()
    video.processed_file.name = ""
    with pytest.raises(FileNotFoundError):
        resolve_hls_source(video, VideoArtifactKind.PROCESSED)


def test_raw_hls_uses_raw_source() -> None:
    video = _video()
    video.raw_file.name = "sensitive_videos/raw.mp4"
    assert resolve_hls_source(video, VideoArtifactKind.RAW).field_file is video.raw_file


def test_live_model_has_no_extra_playback_fields() -> None:
    assert not any(
        field.name.startswith("optimized_playback") for field in VideoFile._meta.fields
    )

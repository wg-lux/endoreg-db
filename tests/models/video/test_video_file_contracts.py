from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, cast
from unittest.mock import Mock, patch

import pytest
from django.core.files.base import ContentFile, File

from endoreg_db.config.env import DEFAULT_VIDEO_FPS
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.medical.hardware.endoscopy_processor import EndoscopyProcessor
from endoreg_db.models.metadata.video_meta import VideoMeta
from endoreg_db.models.media.video.storage_mode import VideoStorageMode
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.video_files import (
    get_video_import_context_names,
    get_video_import_processor,
)


class _WritableFieldFile(Protocol):
    def save(self, name: str, content: File[bytes], save: bool = True) -> None: ...


def _field_file(field: object) -> _WritableFieldFile:
    return cast(_WritableFieldFile, field)


@pytest.fixture
def video_center() -> Center:
    return Center.objects.create(name="video-contracts", display_name="Video Contracts")


@pytest.mark.django_db
def test_video_queryset_next_after_orders_by_primary_key(video_center: Center) -> None:
    first = VideoFile.objects.create(center=video_center, video_hash="query-first")
    second = VideoFile.objects.create(center=video_center, video_hash="query-second")

    video_manager = cast(Any, VideoFile.objects)
    assert video_manager.next_after() == first
    assert video_manager.next_after(first.pk) == second
    assert video_manager.next_after("not-an-int") is None


@pytest.mark.django_db
def test_video_file_hash_lookup_helpers(video_center: Center):
    video = VideoFile.objects.create(center=video_center, video_hash="known-hash")

    assert VideoFile.check_hash_exists("known-hash") is True
    assert VideoFile.check_hash_exists("missing-hash") is False
    assert VideoFile.get_video_by_pk(video.pk) == video
    assert VideoFile.get_video_by_content_hash("known-hash") == video


@pytest.mark.django_db
def test_video_file_import_context_names_prefer_video_processor(
    video_center: Center,
):
    canonical_processor = EndoscopyProcessor.objects.create(name="processor-canonical")
    legacy_processor = EndoscopyProcessor.objects.create(name="processor-legacy")
    video_meta = VideoMeta.objects.create(
        center=video_center,
        processor=legacy_processor,
    )
    video = VideoFile.objects.create(
        center=video_center,
        processor=canonical_processor,
        video_meta=video_meta,
        video_hash="import-context-canonical",
    )

    assert get_video_import_processor(video) == canonical_processor
    assert get_video_import_context_names(video) == (
        video_center.name,
        canonical_processor.name,
    )


@pytest.mark.django_db
def test_video_file_import_context_names_fall_back_to_video_meta_processor(
    video_center: Center,
):
    legacy_processor = EndoscopyProcessor.objects.create(name="processor-meta")
    video_meta = VideoMeta.objects.create(
        center=video_center,
        processor=legacy_processor,
    )
    video = VideoFile.objects.create(
        center=video_center,
        video_meta=video_meta,
        video_hash="import-context-meta",
    )

    assert get_video_import_processor(video) == legacy_processor
    assert get_video_import_context_names(video) == (
        video_center.name,
        legacy_processor.name,
    )


@pytest.mark.django_db
def test_video_file_active_file_prefers_processed_over_raw(video_center: Center):
    video = VideoFile.objects.create(center=video_center, video_hash="active-file")
    _field_file(video.raw_file).save("raw/active.mp4", ContentFile(b"raw"), save=True)
    _field_file(video.processed_file).save(
        "processed/active.mp4", ContentFile(b"processed"), save=True
    )

    assert video.has_raw is True
    assert video.is_processed is True
    assert video.active_file == video.processed_file

    video.processed_file.delete(save=False)
    video.processed_file.name = ""
    assert video.active_file == video.raw_file


@pytest.mark.django_db
def test_video_file_active_file_raises_when_no_media_is_available(video_center: Center):
    video = VideoFile.objects.create(center=video_center, video_hash="no-media")

    with pytest.raises(ValueError, match="neither raw nor processed"):
        _ = video.active_file

    with pytest.raises(ValueError, match=VideoFile.NO_ACTIVE_FILE):
        _ = video.active_raw_file


@pytest.mark.django_db
def test_video_file_active_file_path_uses_processed_stream_path(
    video_center: Center, tmp_path: Path
) -> None:
    processed_path = tmp_path / "processed.mp4"
    processed_path.write_bytes(b"processed")
    video = VideoFile.objects.create(center=video_center, video_hash="active-path")
    video.raw_file.name = "raw/active-path.mp4"
    video.processed_file.name = "processed/active-path.mp4"

    with patch.object(
        video,
        "get_processed_stream_path",
        return_value=processed_path,
    ) as processed_stream_path:
        assert video.active_file_path == processed_path

    processed_stream_path.assert_called_once_with()


@pytest.mark.django_db
def test_video_file_protected_urls_require_streamable_paths(video_center: Center):
    video = VideoFile.objects.create(
        center=video_center,
        video_hash="stream-url",
        storage_mode=VideoStorageMode.STREAMABLE.value,
    )
    video.raw_file.name = "raw/source.mp4"

    assert video.active_raw_file_url is None

    video.raw_streamable_relative_path = "streamable/raw/source.mp4"
    with patch(
        "endoreg_db.services.video_files.streaming.reverse",
        return_value=f"/api/media/videos/{video.pk}/stream/",
    ):
        raw_url = video.active_raw_file_url

    assert raw_url is not None
    assert raw_url.endswith(f"/api/media/videos/{video.pk}/stream/")


@pytest.mark.django_db
def test_video_file_active_file_url_prefers_processed_stream(video_center: Center):
    video = VideoFile.objects.create(
        center=video_center,
        video_hash="processed-url",
        storage_mode=VideoStorageMode.STREAMABLE.value,
    )
    video.raw_file.name = "raw/source.mp4"
    video.processed_file.name = "processed/source.mp4"
    video.raw_streamable_relative_path = "streamable/raw/source.mp4"
    video.processed_streamable_relative_path = "streamable/processed/source.mp4"

    with patch(
        "endoreg_db.services.video_files.streaming.reverse",
        return_value=f"/api/media/videos/{video.pk}/stream/",
    ):
        active_file_url = video.active_file_url

    assert active_file_url is not None
    assert active_file_url.endswith(
        f"/api/media/videos/{video.pk}/stream/?type=processed"
    )


@pytest.mark.django_db
def test_video_file_stream_relative_paths_reject_unsafe_values(video_center: Center):
    video = VideoFile.objects.create(center=video_center, video_hash="unsafe-stream")
    video.raw_streamable_relative_path = "../escape.mp4"
    video.processed_streamable_relative_path = "/absolute/escape.mp4"

    assert video.get_raw_stream_relative_path() is None
    assert video.get_processed_stream_relative_path() is None
    assert video.can_offload_stream_with_nginx("raw") is False


@pytest.mark.django_db
def test_video_file_can_offload_stream_only_in_streamable_mode(
    video_center: Center,
    tmp_path: Path,
) -> None:
    video = VideoFile.objects.create(
        center=video_center,
        video_hash="can-offload",
        storage_mode=VideoStorageMode.ENCRYPTED.value,
    )
    video.raw_streamable_relative_path = "streamable/raw/source.mp4"
    stream_path = tmp_path / "source.mp4"
    stream_path.write_bytes(b"\x00\x00\x00\x18ftypmp42")

    assert video.can_offload_stream_with_nginx("raw") is False

    video.storage_mode = VideoStorageMode.STREAMABLE.value
    with patch.object(video, "get_raw_stream_path", return_value=stream_path):
        assert video.can_offload_stream_with_nginx("raw") is True

    video.storage_mode = "invalid"
    assert video.can_offload_stream_with_nginx("raw") is False


@pytest.mark.django_db
def test_video_file_resolve_processed_stream_source_prefers_streamable_path(
    video_center: Center,
    tmp_path: Path,
) -> None:
    stream_path = tmp_path / "streamable-processed.mp4"
    stream_path.write_bytes(b"processed")
    video = VideoFile.objects.create(
        center=video_center, video_hash="resolve-processed"
    )
    video.processed_file.name = "processed/source.mp4"

    with patch.object(video, "get_processed_stream_path", return_value=stream_path):
        field_file, local_path = video.resolve_video_stream_source("processed")

    assert field_file == video.processed_file
    assert local_path == stream_path


@pytest.mark.django_db
def test_video_file_resolve_raw_stream_source_materializes_when_requested(
    video_center: Center,
    tmp_path: Path,
) -> None:
    stream_path = tmp_path / "streamable-raw.mp4"
    stream_path.write_bytes(b"raw")
    video = VideoFile.objects.create(center=video_center, video_hash="resolve-raw")
    video.raw_file.name = "raw/source.mp4"
    get_raw_stream_path = Mock(side_effect=[None, stream_path])

    with (
        patch.object(video, "get_raw_stream_path", get_raw_stream_path),
        patch(
            "endoreg_db.services.video_files.streaming.sync_video_streamable_artifacts",
            Mock(),
        ) as sync_mock,
    ):
        field_file, local_path = video.resolve_video_stream_source(
            "raw",
            materialize_if_missing=True,
        )

    assert field_file == video.raw_file
    assert local_path == stream_path
    sync_mock.assert_called_once_with(
        video,
        include_raw=True,
        include_processed=False,
        save=True,
    )


@pytest.mark.django_db
def test_video_file_resolve_stream_source_raises_when_media_missing(
    video_center: Center,
):
    video = VideoFile.objects.create(center=video_center, video_hash="missing-stream")

    with pytest.raises(FileNotFoundError, match="No processed file"):
        video.resolve_video_stream_source("processed")

    with pytest.raises(ValueError, match=VideoFile.NO_ACTIVE_FILE):
        video.resolve_video_stream_source("raw")


@pytest.mark.django_db
def test_video_file_get_or_create_state_persists_relation(video_center: Center):
    video = VideoFile.objects.create(center=video_center, video_hash="stateful")

    state = video.get_or_create_state()
    video.refresh_from_db()

    assert state.pk is not None
    assert video.state == state
    assert video.get_or_create_state() == state


@pytest.mark.django_db
def test_video_file_ensure_default_fps_persists_once(video_center: Center):
    video = VideoFile.objects.create(center=video_center, video_hash="default-fps")

    assert video.ensure_default_fps() == VideoFile.default_fps
    video.refresh_from_db()
    assert video.fps == VideoFile.default_fps

    with patch.object(video, "save", side_effect=AssertionError("must not save twice")):
        assert video.ensure_default_fps() == VideoFile.default_fps


@pytest.mark.django_db
def test_video_file_get_fps_defaults_to_50_when_missing(video_center: Center):
    video = VideoFile.objects.create(center=video_center, video_hash="get-fps-default")

    assert VideoFile.default_fps == DEFAULT_VIDEO_FPS
    assert VideoFile.use_default_fps is True
    assert video.get_fps() == DEFAULT_VIDEO_FPS

    video.refresh_from_db()
    assert video.fps == DEFAULT_VIDEO_FPS


@pytest.mark.django_db
def test_video_file_frame_number_to_seconds_uses_existing_or_loaded_fps(
    video_center: Center,
):
    video = VideoFile.objects.create(
        center=video_center, video_hash="frame-time", fps=25
    )

    assert video.frame_number_to_s(50) == 2.0

    video.fps = None
    with patch.object(video, "get_fps", return_value=10):
        assert video.frame_number_to_s(25) == 2.5

    with patch.object(video, "get_fps", return_value=0):
        with pytest.raises(ValueError, match="FPS must be set"):
            video.frame_number_to_s(25)

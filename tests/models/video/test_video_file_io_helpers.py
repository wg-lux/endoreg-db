from __future__ import annotations

# pyright: reportPrivateUsage=false

from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, patch

import pytest

from django.core.files.storage import Storage
from django.db import models

from endoreg_db.utils.storage.video_fields import VideoArtifactFieldFile
from endoreg_db.models import VideoFile
from endoreg_db.services.video_files import io as video_file_io


class _PathlessFieldFile(VideoArtifactFieldFile):
    def __init__(self, name: str):
        super().__init__(VideoFile(), models.FileField(storage=Storage()), name)


@pytest.mark.unit
def test_get_raw_file_path_returns_none_for_pathless_storage(tmp_path: Path) -> None:
    sensitive_dir = tmp_path / "sensitive"
    sensitive_dir.mkdir()
    raw_path = sensitive_dir / "raw-video.mp4"
    raw_path.write_bytes(b"raw")
    video = SimpleNamespace(
        has_raw=True,
        raw_file=_PathlessFieldFile("stale/prefix/raw-video.mp4"),
        raw_video_hash="raw-hash",
    )

    resolved = video_file_io.get_raw_video_file_path(cast(VideoFile, video))

    assert resolved is None


@pytest.mark.unit
def test_get_raw_file_path_returns_none_without_raw_reference() -> None:
    video = SimpleNamespace(
        has_raw=False, raw_file=_PathlessFieldFile(""), raw_video_hash="none"
    )

    assert video_file_io.get_raw_video_file_path(cast(VideoFile, video)) is None


@pytest.mark.unit
def test_get_processed_file_path_returns_none_for_pathless_storage(
    tmp_path: Path,
) -> None:
    storage_dir = tmp_path / "storage"
    processed_path = storage_dir / "processed" / "video.mp4"
    processed_path.parent.mkdir(parents=True)
    processed_path.write_bytes(b"processed")
    video = SimpleNamespace(
        is_processed=True,
        processed_file=_PathlessFieldFile("processed/video.mp4"),
        raw_video_hash="processed-hash",
    )

    with patch.object(
        video_file_io, "get_processed_video_stream_path", return_value=None
    ):
        resolved = video_file_io.get_processed_video_file_path(cast(VideoFile, video))

    assert resolved is None


@pytest.mark.unit
def test_get_processed_file_path_reports_remote_only_storage_without_local_path() -> (
    None
):
    field_file = _PathlessFieldFile("remote/video.mp4")
    video = SimpleNamespace(
        is_processed=True,
        processed_file=field_file,
        raw_video_hash="remote-hash",
    )

    with patch.object(
        video_file_io, "get_processed_video_stream_path", return_value=None
    ):
        resolved = video_file_io.get_processed_video_file_path(cast(VideoFile, video))

    assert resolved is None


@pytest.mark.unit
def test_get_processed_stream_path_materializes_when_requested(tmp_path: Path) -> None:
    stream_path = tmp_path / "streamable.mp4"
    stream_path.write_bytes(b"streamable")
    video = SimpleNamespace(processed_streamable_relative_path="")

    with (
        patch.object(
            video_file_io,
            "resolve_streamable_path",
            side_effect=[None, stream_path],
        ),
        patch(
            "endoreg_db.services.streamable_media.sync_video_streamable_artifacts",
            Mock(),
        ) as sync_mock,
    ):
        resolved = video_file_io.get_processed_video_stream_path(
            cast(VideoFile, video),
            materialize_if_missing=True,
        )

    assert resolved == stream_path
    sync_mock.assert_called_once_with(
        video,
        include_raw=False,
        include_processed=True,
        save=True,
    )


@pytest.mark.unit
def test_ensure_local_helpers_fail_when_required_file_state_is_missing() -> None:
    rawless = SimpleNamespace(has_raw=False, raw_video_hash="rawless")
    processedless = SimpleNamespace(is_processed=False, raw_video_hash="processedless")

    with pytest.raises(ValueError, match="has no raw file"):
        with video_file_io.ensure_local_raw_video_file(cast(VideoFile, rawless)):
            pass

    with pytest.raises(ValueError, match="has no processed file"):
        with video_file_io.ensure_local_processed_video_file(
            cast(VideoFile, processedless)
        ):
            pass


@pytest.mark.unit
def test_delete_raw_file_after_validation_deletes_field_file_via_storage() -> None:
    raw_field = SimpleNamespace(name="raw.mp4")
    video = SimpleNamespace(
        raw_file=raw_field, raw_streamable_relative_path="", save=Mock()
    )

    with (
        patch.object(video_file_io, "get_raw_video_stream_path", return_value=None),
        patch.object(video_file_io, "delete_field_file", return_value=True) as delete,
        patch(
            "endoreg_db.services.hls_media.delete_video_hls_artifacts",
            return_value=False,
        ) as delete_hls,
    ):
        deleted = video_file_io.delete_raw_file_after_validation(cast(VideoFile, video))

    assert deleted is True
    delete_hls.assert_called_once_with(video, artifact_kind="raw")
    delete.assert_called_once_with(video, "raw_file", missing_ok=True, save=True)
    video.save.assert_not_called()

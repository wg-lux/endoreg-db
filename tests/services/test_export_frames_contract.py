from __future__ import annotations

# pyright: reportPrivateUsage=false

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest
from django.db.models import QuerySet
from django.test.utils import override_settings

from endoreg_db.export.frames import export_frames_with_labels as export_module
from endoreg_db.models.label.annotation.image_classification import (
    ImageClassificationAnnotation,
)
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.utils.file_operations import atomic_write_file


class _FakeValuesList:
    def __init__(self, values: list[int]) -> None:
        self._values = values

    def distinct(self) -> "_FakeValuesList":
        return self

    def order_by(self, *fields: str) -> list[int]:
        return self._values


class _FakeAnnotations:
    def __init__(self, video_id: int) -> None:
        self._video_id = video_id

    def values_list(self, *fields: str, flat: bool) -> _FakeValuesList:
        return _FakeValuesList([self._video_id])


class _FakeFrameAnnotations:
    def __init__(self, annotation: "_AnnotationDouble") -> None:
        self._annotation = annotation

    def iterator(self) -> Iterator["_AnnotationDouble"]:
        yield self._annotation


class _FakeFrameRows:
    def __init__(self, frames: list["_FrameDouble"]) -> None:
        self._frames = frames

    def only(self, *fields: str) -> list["_FrameDouble"]:
        assert fields == ("pk", "frame_number")
        return self._frames


@dataclass(frozen=True)
class _NamedFile:
    name: str


@dataclass(frozen=True)
class _VideoState:
    anonymization_validated: bool = True
    processing_error: bool = False
    outside_segments_removed: bool = False
    ready_for_export: bool = False
    processed_file_sha256: str = ""


@dataclass
class _VideoDouble:
    pk: int
    video_hash: str = "raw-hash"
    raw_file: _NamedFile = field(default_factory=lambda: _NamedFile(""))
    processed_file: _NamedFile = field(default_factory=lambda: _NamedFile(""))
    active_file: _NamedFile = field(default_factory=lambda: _NamedFile(""))
    state: _VideoState = field(default_factory=_VideoState)
    frames: _FakeFrameRows | None = None

    def get_frame_dir_path(self) -> Path:
        raise AssertionError("canonical frame dir must not be used")


@dataclass
class _FrameDouble:
    pk: int
    video: VideoFile
    relative_path: str
    file_path: str
    frame_number: int = 0
    timestamp: float = 0.0


@dataclass
class _AnnotationDouble:
    frame: _FrameDouble


def test_export_videos_prefers_processed_artifact_over_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)

    raw_path = storage_dir / "sensitive_videos" / "raw-source.mp4"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(b"raw-video-bytes")

    processed_path = storage_dir / "anonymized_videos" / "processed-final.mp4"
    processed_path.parent.mkdir(parents=True, exist_ok=True)
    processed_path.write_bytes(b"processed-video-bytes")

    video = _VideoDouble(
        pk=7,
        video_hash="raw-hash",
        raw_file=_NamedFile(
            name=raw_path.relative_to(storage_dir).as_posix(),
        ),
        processed_file=_NamedFile(
            name=processed_path.relative_to(storage_dir).as_posix(),
        ),
        active_file=_NamedFile(
            name=raw_path.relative_to(storage_dir).as_posix(),
        ),
        state=_VideoState(anonymization_validated=True),
    )

    output_dir = tmp_path / "exported"
    output_dir.mkdir(parents=True, exist_ok=True)

    def resolve_existing_media_path(name: str) -> Path | None:
        media_path = storage_dir / name
        return media_path if media_path.exists() else None

    def filter_videos_by_id(**kwargs: _FakeValuesList) -> list[VideoFile]:
        return [cast(VideoFile, video)]

    monkeypatch.setattr(
        export_module,
        "resolve_existing_protected_media_path",
        resolve_existing_media_path,
    )
    monkeypatch.setattr(export_module.VideoFile.objects, "filter", filter_videos_by_id)

    exported_count = export_module._export_videos_from_annotations(
        cast(QuerySet[ImageClassificationAnnotation], _FakeAnnotations(video.pk)),
        output_dir=output_dir,
    )

    assert exported_count == 1
    exported_file = output_dir / f"video_{video.pk}_{video.video_hash}.mp4"
    assert exported_file.read_bytes() == b"processed-video-bytes"


def test_export_config_defaults_are_safe() -> None:
    config = export_module.export_config(output_path="frames.csv")

    assert config.export_frames is True
    assert config.export_videos is False
    assert config.use_export_flags is True
    assert config.only_validated is True


def test_export_videos_rejects_unvalidated_media(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = _VideoDouble(
        pk=7,
        video_hash="raw-hash",
        processed_file=_NamedFile(name="processed-final.mp4"),
        state=_VideoState(anonymization_validated=False),
    )

    def filter_videos_by_id(**kwargs: _FakeValuesList) -> list[VideoFile]:
        return [cast(VideoFile, video)]

    monkeypatch.setattr(export_module.VideoFile.objects, "filter", filter_videos_by_id)

    with pytest.raises(ValueError, match="not human anonymization validated"):
        export_module._export_videos_from_annotations(
            cast(QuerySet[ImageClassificationAnnotation], _FakeAnnotations(video.pk)),
            output_dir=tmp_path,
        )


def test_export_videos_rejects_failed_lost_media() -> None:
    video = _VideoDouble(
        pk=7,
        state=_VideoState(
            processing_error=True,
            anonymization_validated=True,
        ),
    )

    with pytest.raises(ValueError, match="failed/lost"):
        export_module._assert_video_media_export_ready(cast(VideoFile, video))


@override_settings(ENDOREG_DEPLOYMENT_ROLE="local_study_server")
def test_local_frame_asset_export_forces_processed_transcode() -> None:
    config = export_module.export_config(
        output_path="frames.csv",
        center_key="center-a",
        export_frames=True,
        transcode_frames=False,
        transcode_overwrite=False,
        use_frame_pk_paths=False,
    )

    normalized = export_module._normalize_export_config(config)

    assert normalized.transcode_frames is True
    assert normalized.transcode_overwrite is True
    assert normalized.use_frame_pk_paths is True


@override_settings(ENDOREG_DEPLOYMENT_ROLE="local_study_server")
def test_local_frame_asset_export_rejects_unready_media(tmp_path: Path) -> None:
    video = _VideoDouble(
        pk=11,
        state=_VideoState(
            anonymization_validated=True,
            outside_segments_removed=True,
            ready_for_export=False,
            processed_file_sha256="",
        ),
    )
    frame = _FrameDouble(
        pk=4,
        video=cast(VideoFile, video),
        relative_path="frames/frame_4.jpg",
        file_path="frames/frame_4.jpg",
    )
    annotation = _AnnotationDouble(frame=frame)

    with pytest.raises(ValueError, match="ready_for_export"):
        export_module._export_frames_from_annotations(
            cast(
                QuerySet[ImageClassificationAnnotation],
                _FakeFrameAnnotations(annotation),
            ),
            output_dir=tmp_path,
            use_frame_pk_paths=True,
            frame_ext="jpg",
            generated_frame_root=tmp_path / "generated_frames",
        )


@override_settings(ENDOREG_DEPLOYMENT_ROLE="local_study_server")
def test_local_transcode_uses_export_scoped_frame_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "processed.mp4"
    atomic_write_file(
        destination=source_path,
        content=[b"processed"],
        required_bytes=len(b"processed"),
    )
    captured_frame_dir: list[Path] = []
    captured_source_path: list[Path] = []
    video = _VideoDouble(pk=21)

    def assert_video_media_export_ready(video: VideoFile) -> None:
        return None

    def resolve_processed_video_source_path(video: VideoFile) -> Path:
        return source_path

    def fake_extract(
        video: VideoFile,
        *,
        source_path: Path,
        frame_dir: Path,
        frame_pks: set[int] | None,
        fps: float,
        quality: int,
        ext: str,
        overwrite: bool,
    ) -> None:
        captured_frame_dir.append(frame_dir)
        captured_source_path.append(source_path)

    monkeypatch.setattr(
        export_module,
        "_assert_video_media_export_ready",
        assert_video_media_export_ready,
    )
    monkeypatch.setattr(
        export_module,
        "_resolve_processed_video_source_path",
        resolve_processed_video_source_path,
    )

    monkeypatch.setattr(
        export_module,
        "_extract_and_move_transcoded_frames",
        fake_extract,
    )

    export_root = tmp_path / "export-generated"
    export_module._transcode_video_to_frame_dir(
        cast(VideoFile, video),
        frame_pks={1},
        fps=25.0,
        quality=2,
        ext="jpg",
        overwrite=True,
        export_frame_root=export_root,
    )

    assert captured_frame_dir == [export_root / "video_21"]
    assert captured_source_path == [source_path]


@override_settings(ENDOREG_DEPLOYMENT_ROLE="local_study_server")
def test_local_frame_source_does_not_fallback_to_canonical_storage(
    tmp_path: Path,
) -> None:
    video = _VideoDouble(pk=31)
    frame = _FrameDouble(
        pk=5,
        video=cast(VideoFile, video),
        relative_path="canonical/frame_5.jpg",
        file_path="canonical/frame_5.jpg",
    )
    generated_root = tmp_path / "generated"

    source_path = export_module._resolve_frame_source_path(
        frame,
        frame_relative_path="frame_5.jpg",
        use_frame_pk_paths=True,
        frame_ext="jpg",
        generated_frame_root=generated_root,
    )

    assert source_path == generated_root / "video_31" / "frame_5.jpg"


def test_legacy_transcode_fps_cannot_reindex_annotation_frames(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "processed.mp4"
    source_path.write_bytes(b"processed")
    frame = _FrameDouble(
        pk=41,
        video=cast(VideoFile, None),
        relative_path="frame_0000010.jpg",
        file_path="frame_0000010.jpg",
        frame_number=10,
        timestamp=0.417,
    )
    video = _VideoDouble(pk=21, frames=_FakeFrameRows([frame]))
    captured: dict[str, int] = {}

    def fake_extract_range(
        video_path: Path,
        output_dir: Path,
        start_frame: int,
        end_frame: int,
        quality: int,
        ext: str,
    ) -> list[Path]:
        assert video_path == source_path
        assert quality == 2
        captured.update(start_frame=start_frame, end_frame=end_frame)
        output = output_dir / f"frame_{start_frame:07d}.{ext}"
        output.write_bytes(b"frame-10")
        return [output]

    def reject_fps_resample(*args: object, **kwargs: object) -> list[Path]:
        raise AssertionError("legacy transcode_fps must not resample source frames")

    monkeypatch.setattr(export_module, "ffmpeg_extract_frame_range", fake_extract_range)
    monkeypatch.setattr(export_module, "ffmpeg_extract_frames", reject_fps_resample)

    export_module._extract_and_move_transcoded_frames(
        cast(VideoFile, video),
        source_path=source_path,
        frame_dir=tmp_path / "frames",
        frame_pks={41},
        fps=50.0,
        quality=2,
        ext="jpg",
        overwrite=True,
    )

    assert captured == {"start_frame": 10, "end_frame": 11}
    assert (tmp_path / "frames" / "frame_41.jpg").read_bytes() == b"frame-10"

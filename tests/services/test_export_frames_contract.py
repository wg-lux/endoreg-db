from __future__ import annotations

# pyright: reportPrivateUsage=false

from collections.abc import Generator, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from django.db.models import QuerySet
from django.test.utils import override_settings

from endoreg_db.export.frames import export_frames_with_labels as export_module
from endoreg_db.models.label.annotation.image_classification import (
    ImageClassificationAnnotation,
)
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models import (
    Center,
    Frame,
    InformationSource,
    Label,
    VideoState,
)
from endoreg_db.schemas.video_storage import (
    VideoArtifactProbe,
    VideoSourceTimelineEvidence,
    VideoTimelineContract,
)
from endoreg_db.services.video_storage.contracts import evidence_as_json
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
        assert fields in {
            ("pk", "frame_number"),
            ("pk", "frame_number", "presentation_timestamp"),
            ("pk", "presentation_timestamp"),
        }
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
    meta: dict[str, object] | None = None

    def get_frame_dir_path(self) -> Path:
        raise AssertionError("canonical frame dir must not be used")


@dataclass
class _FrameDouble:
    pk: int
    video: VideoFile
    relative_path: str
    file_path: str
    frame_number: int = 0
    timestamp: float | None = 0.0
    presentation_timestamp: int | None = None


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


def test_pts_dataset_forces_frames_from_processed_artifact() -> None:
    config = export_module.export_config(
        output_path="annotations.json",
        export_profile="pts_dataset_v1",
        export_frames=True,
        transcode_frames=False,
        transcode_overwrite=False,
        use_frame_pk_paths=False,
    )

    normalized = export_module._normalize_export_config(config)

    assert normalized.transcode_frames is True
    assert normalized.transcode_overwrite is True
    assert normalized.use_frame_pk_paths is True


@pytest.mark.django_db
def test_pts_dataset_export_preserves_exact_timeline_and_deduplicates_image_ids(
    tmp_path: Path,
) -> None:
    center = Center.objects.create(name="pts-export-center")
    state = VideoState.objects.create(
        anonymization_validated=True,
        processed_file_sha256="a" * 64,
    )
    timeline = VideoTimelineContract(
        fps_num=30_000,
        fps_den=1_001,
        duration_seconds=2.0,
        frame_count=2,
        variable_frame_rate=True,
        time_base_num=1,
        time_base_den=90_000,
    )
    probe = VideoArtifactProbe(
        codec_name="h264",
        pixel_format="yuv420p",
        width=640,
        height=480,
        size_bytes=1024,
        timeline=timeline,
    )
    evidence = VideoSourceTimelineEvidence(
        persisted_at=datetime.now(UTC),
        source=probe,
        timestamp_mapping="ffprobe_pts",
    )
    video = VideoFile.objects.create(
        center=center,
        state=state,
        video_hash="pts-export-video",
        fps=timeline.fps,
        duration=timeline.duration_seconds,
        frame_count=timeline.frame_count,
        meta={"source_timeline": evidence_as_json(evidence)},
    )
    later_frame = Frame.objects.create(
        video=video,
        frame_number=1,
        relative_path="frame_0000001.jpg",
        timestamp=1.602488,
        presentation_timestamp=144_224,
    )
    earlier_frame = Frame.objects.create(
        video=video,
        frame_number=0,
        relative_path="frame_0000000.jpg",
        timestamp=0.5,
        presentation_timestamp=45_000,
    )
    source = InformationSource.objects.create(name="pts-export-source")
    first_label = Label.objects.create(name="pts-export-label-a")
    second_label = Label.objects.create(name="pts-export-label-b")
    ImageClassificationAnnotation.objects.create(
        frame=later_frame,
        label=first_label,
        value=True,
        information_source=source,
    )
    ImageClassificationAnnotation.objects.create(
        frame=earlier_frame,
        label=first_label,
        value=True,
        information_source=source,
    )
    ImageClassificationAnnotation.objects.create(
        frame=earlier_frame,
        label=second_label,
        value=True,
        information_source=source,
    )

    output_path = tmp_path / "annotations.json"
    export_module.export_frames_with_labels_to_json(
        output_path,
        export_profile="pts_dataset_v1",
        video_id=video.pk,
        information_source_name=source.name,
        only_validated=False,
        use_export_flags=False,
        export_frames=False,
    )

    rows = json.loads(output_path.read_text())
    assert [row["presentation_timestamp"] for row in rows] == [
        45_000,
        45_000,
        144_224,
    ]
    assert [row["export_frame_index"] for row in rows] == [1, 1, 2]
    assert rows[0]["stream_time_base_num"] == 1
    assert rows[0]["stream_time_base_den"] == 90_000
    assert rows[0]["timeline_version"] == "pts_v1"
    assert rows[0]["artifact_kind"] == "processed"
    assert rows[0]["artifact_sha256"] == "a" * 64


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


def test_pts_transcode_uses_seekable_encrypted_processed_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = _VideoDouble(
        pk=22,
        processed_file=_NamedFile(name="anonymized/processed.mp4"),
    )
    captured_source: list[Path | str] = []

    def fake_ready(_video: VideoFile) -> None:
        return None

    def no_plaintext_path(_video: VideoFile) -> Path | None:
        return None

    def has_range_storage(_field_file: object) -> bool:
        return True

    monkeypatch.setattr(
        export_module,
        "_assert_video_media_export_ready",
        fake_ready,
    )
    monkeypatch.setattr(
        export_module,
        "_resolve_processed_video_source_path",
        no_plaintext_path,
    )
    monkeypatch.setattr(
        export_module,
        "field_file_has_decrypted_range_storage",
        has_range_storage,
    )

    @contextmanager
    def fake_seekable_input(
        _field_file: object,
    ) -> Generator[SimpleNamespace, None, None]:
        yield SimpleNamespace(url="http://127.0.0.1:43123/media-token")

    def fake_extract(
        _video: VideoFile,
        *,
        source_path: Path | str,
        frame_dir: Path,
        frame_pks: set[int] | None,
        fps: float,
        quality: int,
        ext: str,
        overwrite: bool,
    ) -> None:
        captured_source.append(source_path)

    monkeypatch.setattr(
        export_module, "serve_seekable_media_input", fake_seekable_input
    )
    monkeypatch.setattr(
        export_module,
        "_extract_and_move_transcoded_frames",
        fake_extract,
    )

    def reject_materialization(_field_file: object) -> AbstractContextManager[Path]:
        raise AssertionError("full processed artifact must not be materialized")

    monkeypatch.setattr(
        export_module,
        "ensure_local_file",
        reject_materialization,
    )

    export_module._transcode_video_to_frame_dir(
        cast(VideoFile, video),
        frame_pks={1},
        fps=25.0,
        quality=2,
        ext="jpg",
        overwrite=True,
        export_frame_root=tmp_path / "generated",
    )

    assert captured_source == ["http://127.0.0.1:43123/media-token"]


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


def test_transcode_extracts_sparse_frames_by_exact_presentation_timestamp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "processed.mp4"
    source_path.write_bytes(b"processed")
    first = _FrameDouble(
        pk=51,
        video=cast(VideoFile, None),
        relative_path="frame_0000010.jpg",
        file_path="frame_0000010.jpg",
        frame_number=10,
        timestamp=0.5,
        presentation_timestamp=45_000,
    )
    second = _FrameDouble(
        pk=52,
        video=cast(VideoFile, None),
        relative_path="frame_0001000.jpg",
        file_path="frame_0001000.jpg",
        frame_number=1000,
        timestamp=1.602488,
        presentation_timestamp=144_224,
    )
    video = _VideoDouble(pk=22, frames=_FakeFrameRows([first, second]))
    video.meta = {
        "source_timeline": evidence_as_json(
            VideoSourceTimelineEvidence(
                persisted_at=datetime.now(UTC),
                source=VideoArtifactProbe(
                    codec_name="h264",
                    pixel_format="yuv420p",
                    width=640,
                    height=480,
                    size_bytes=1024,
                    timeline=VideoTimelineContract(
                        fps_num=30_000,
                        fps_den=1_001,
                        duration_seconds=2.0,
                        frame_count=2,
                        variable_frame_rate=True,
                        time_base_num=1,
                        time_base_den=90_000,
                    ),
                ),
                timestamp_mapping="ffprobe_pts",
            )
        )
    }
    first.video = cast(VideoFile, video)
    second.video = cast(VideoFile, video)
    captured_pts: list[int] = []

    def fake_extract_by_pts(
        video_path: Path,
        output_dir: Path,
        presentation_timestamps: list[int],
        time_base_num: int,
        time_base_den: int,
        quality: int,
        ext: str,
    ) -> list[Path]:
        assert video_path == source_path
        assert quality == 2
        assert (time_base_num, time_base_den) == (1, 90_000)
        captured_pts.extend(presentation_timestamps)
        paths: list[Path] = []
        for index in range(len(presentation_timestamps)):
            output = output_dir / f"frame_{index:07d}.{ext}"
            output.write_bytes(f"frame-{index}".encode())
            paths.append(output)
        return paths

    def reject_range(*args: object, **kwargs: object) -> list[Path]:
        raise AssertionError("sparse PTS export must not decode an ordinal range")

    monkeypatch.setattr(
        export_module, "ffmpeg_extract_frames_by_pts", fake_extract_by_pts
    )
    monkeypatch.setattr(export_module, "ffmpeg_extract_frame_range", reject_range)

    export_module._extract_and_move_transcoded_frames(
        cast(VideoFile, video),
        source_path=source_path,
        frame_dir=tmp_path / "frames",
        frame_pks={51, 52},
        fps=50.0,
        quality=2,
        ext="jpg",
        overwrite=True,
    )

    assert captured_pts == [45_000, 144_224]
    assert (tmp_path / "frames" / "frame_51.jpg").read_bytes() == b"frame-0"
    assert (tmp_path / "frames" / "frame_52.jpg").read_bytes() == b"frame-1"

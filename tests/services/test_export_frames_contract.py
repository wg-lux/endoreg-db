from __future__ import annotations

import csv
import json

# pyright: reportPrivateUsage=false
from collections.abc import Generator, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, cast

import pytest
from django.db import OperationalError, close_old_connections, connection, transaction
from django.db.models import QuerySet
from django.test.utils import override_settings
from lx_dtypes.models.contracts.video_provenance import (
    VideoFrameProvenancePayload,
    VideoFrameTimeline,
    VideoProvenanceBundle,
)

from endoreg_db.export.frames import export_frames_with_labels as export_module
from endoreg_db.models import (
    Center,
    Frame,
    InformationSource,
    Label,
    LabelVideoSegment,
    SensitiveMeta,
    VideoState,
)
from endoreg_db.models.label.annotation.image_classification import (
    ImageClassificationAnnotation,
)
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.schemas.frame_annotation_export import FrameAnnotationExportSegment
from endoreg_db.schemas.video_storage import (
    VideoArtifactProbe,
    VideoSourceTimelineEvidence,
    VideoTimelineContract,
)
from endoreg_db.services.media import export_policy
from endoreg_db.services.media.csv_export import csv_lines
from endoreg_db.services.video_storage.contracts import evidence_as_json
from endoreg_db.utils.file_operations import atomic_write_file
from endoreg_db.utils.hashs import get_file_hash


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
    raw_video_hash: str = "raw-hash"
    processed_video_hash: str = "a" * 64
    raw_file: _NamedFile = field(default_factory=lambda: _NamedFile(""))
    processed_file: _NamedFile = field(default_factory=lambda: _NamedFile(""))
    active_file: _NamedFile = field(default_factory=lambda: _NamedFile(""))
    state: _VideoState = field(default_factory=_VideoState)
    frames: _FakeFrameRows | None = None
    meta: dict[str, object] | None = None
    original_file_name: str | None = None
    sensitive_meta: SensitiveMeta | None = None

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
        raw_video_hash="raw-hash",
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

    def hash_processed_file(_file: object) -> str:
        return get_file_hash(processed_path)

    monkeypatch.setattr(export_policy, "get_file_hash", hash_processed_file)
    video.processed_video_hash = get_file_hash(processed_path)
    exported_count = export_module._export_videos_from_annotations(
        cast(QuerySet[ImageClassificationAnnotation], _FakeAnnotations(video.pk)),
        output_dir=output_dir,
    )

    assert exported_count == 1
    exported_file = output_dir / f"{video.raw_video_hash}.mp4"
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


def test_legacy_transcode_uses_matching_table_and_asset_names() -> None:
    normalized = export_module._normalize_export_config(
        export_module.export_config(
            output_path="annotations.csv",
            transcode_frames=True,
            use_frame_pk_paths=None,
        )
    )
    assert normalized.use_frame_pk_paths is True


@pytest.mark.django_db
@pytest.mark.parametrize("has_source", [False, True])
@pytest.mark.parametrize("use_export_flags", [False, True])
def test_segment_export_filters_nullable_provenance(
    has_source: bool,
    use_export_flags: bool,
) -> None:
    video = VideoFile.objects.create(
        center=Center.objects.create(name="segment-export-center"),
        raw_video_hash="segment-export-video",
        fps=25,
        frame_count=4,
        duration=0.16,
    )
    label = Label.objects.create(name="segment-export-label")
    source = InformationSource.objects.create(name="segment-export-source")
    selected_source = source if has_source else None
    segment = LabelVideoSegment.objects.create(
        video_file=video,
        label=label,
        source=selected_source,
        start_frame_number=1,
        end_frame_number=3,
        export_segment=True,
    )
    expected_ids: list[int] = []
    for frame_number in range(4):
        frame = Frame.objects.create(
            video=video,
            frame_number=frame_number,
            relative_path=f"frame_{frame_number}.jpg",
        )
        matching = ImageClassificationAnnotation.objects.create(
            frame=frame,
            label=label,
            information_source=selected_source,
            value=True,
        )
        ImageClassificationAnnotation.objects.create(
            frame=frame,
            label=label,
            information_source=None if has_source else source,
            value=True,
        )
        if 1 <= frame_number < 3:
            expected_ids.append(matching.pk)

    selected = export_module._filter_annotations_by_segments(
        ImageClassificationAnnotation.objects.all(),
        video_id=video.pk,
        segment_ids=None if use_export_flags else [segment.pk],
        use_export_flags=use_export_flags,
    )

    assert list(selected.order_by("pk").values_list("pk", flat=True)) == expected_ids


@pytest.mark.django_db
@pytest.mark.parametrize("approval_hash", ["a" * 64, "b" * 64, ""])
def test_pts_dataset_export_preserves_exact_timeline_and_deduplicates_image_ids(
    tmp_path: Path,
    approval_hash: str,
) -> None:
    center = Center.objects.create(name="pts-export-center")
    state = VideoState.objects.create(
        anonymization_validated=True,
        processed_file_sha256=approval_hash,
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
        raw_video_hash="pts-export-video",
        processed_video_hash="a" * 64,
        processed_file="processed.mp4",
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
    if approval_hash == "b" * 64:
        with pytest.raises(ValueError, match="readiness proof|Videogeneration"):
            export_module.export_frames_with_labels_to_json(
                output_path,
                export_profile="pts_dataset_v1",
                video_id=video.pk,
                information_source_name=source.name,
                only_validated=False,
                use_export_flags=False,
                export_frames=False,
            )
        assert not output_path.exists()
        return
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
        raw_video_hash="raw-hash",
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
        export_module.assert_video_media_export_ready(cast(VideoFile, video))


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
        "assert_video_media_export_ready",
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

    def exact_coordinates(
        _video: VideoFile,
        *,
        frame_pks: set[int] | None,
    ) -> export_module._RequestedFrameCoordinates:
        assert frame_pks == {1}
        return export_module._RequestedFrameCoordinates({1}, [0], [(0, 1)], False)

    monkeypatch.setattr(
        export_module, "_requested_frame_coordinates", exact_coordinates
    )

    def fake_ready(_video: VideoFile) -> None:
        return None

    def no_plaintext_path(_video: VideoFile) -> Path | None:
        return None

    def has_range_storage(_field_file: object) -> bool:
        return True

    monkeypatch.setattr(
        export_module,
        "assert_video_media_export_ready",
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


@pytest.mark.django_db
@pytest.mark.parametrize("output_format", ["csv", "json"])
def test_export_job_uses_one_annotation_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output_format: str,
) -> None:
    video = VideoFile.objects.create(
        center=Center.objects.create(name="snapshot-center"),
        raw_video_hash="snapshot-video",
    )
    frame = Frame.objects.create(video=video, frame_number=0, relative_path="frame.jpg")
    label = Label.objects.create(name="snapshot-label")
    source = InformationSource.objects.create(name="snapshot-source")
    selected = ImageClassificationAnnotation.objects.create(
        value=True,
        frame=frame,
        label=label,
        information_source=source,
        annotator="=formula",
    )
    original_writer = export_module._write_annotation_rows
    asset_ids: list[int] = []

    def write_then_change_selection(
        path: Path,
        rows: list[export_module.AnnotationRow],
        *,
        output_format: str,
    ) -> Path:
        result = original_writer(path, rows, output_format=output_format)
        ImageClassificationAnnotation.objects.filter(pk=selected.pk).update(
            annotator="changed"
        )
        ImageClassificationAnnotation.objects.create(
            value=True,
            frame=frame,
            label=label,
            information_source=source,
            annotator="new",
        )
        return result

    def capture_assets(
        annotations: list[ImageClassificationAnnotation],
        **kwargs: object,
    ) -> export_module.ExportAssetResult:
        asset_ids.extend(item.pk for item in annotations)
        assert annotations[0].annotator == "=formula"
        return {
            "exported_video_count": 0,
            "exported_frame_count": 1,
            "video_output_dir": None,
            "frame_output_dir": tmp_path / "frames",
        }

    monkeypatch.setattr(
        export_module, "_write_annotation_rows", write_then_change_selection
    )
    monkeypatch.setattr(export_module, "_export_media_assets", capture_assets)
    result = export_module.AnnotationExporterClient().run_export(
        export_module.export_config(
            output_path=tmp_path / f"annotations.{output_format}",
            output_format=cast(Literal["csv", "json"], output_format),
            use_export_flags=False,
            only_validated=False,
        )
    )
    assert result.row_count == 1
    assert asset_ids == [selected.pk]
    if output_format == "json":
        rows = json.loads(result.output_path.read_text())
        assert rows[0]["annotator"] == "=formula"
    else:
        with result.output_path.open() as exported_file:
            rows = list(csv.DictReader(exported_file))
        assert rows[0]["annotator"] == "'=formula"


@pytest.mark.parametrize(
    "value", ["=formula", "+formula", "-formula", "@formula", "normal"]
)
def test_file_and_browser_csv_use_same_text_encoding(
    tmp_path: Path, value: str
) -> None:
    row = cast(
        export_module.AnnotationRow, dict.fromkeys(export_module.DEFAULT_FIELDNAMES)
    )
    row["annotator"] = value
    output = export_module._write_annotation_rows(
        tmp_path / "annotations.csv", [row], output_format="csv"
    )
    assert output.read_bytes().decode() == "".join(
        csv_lines(
            [
                export_module.DEFAULT_FIELDNAMES,
                [row[name] for name in export_module.DEFAULT_FIELDNAMES],
            ]
        )
    )


@pytest.mark.django_db
def test_snapshot_rejects_generation_changed_before_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = VideoFile.objects.create(
        center=Center.objects.create(name="changed-generation-center"),
        raw_video_hash="changed-generation-video",
        processed_video_hash="a" * 64,
    )
    frame = Frame.objects.create(video=video, frame_number=0, relative_path="frame.jpg")
    ImageClassificationAnnotation.objects.create(
        value=True,
        frame=frame,
        label=Label.objects.create(name="changed-generation-label"),
    )
    original_select_for_update = VideoFile.objects.select_for_update

    def replace_before_lock() -> QuerySet[VideoFile]:
        VideoFile.objects.filter(pk=video.pk).update(processed_video_hash="b" * 64)
        return original_select_for_update()

    monkeypatch.setattr(VideoFile.objects, "select_for_update", replace_before_lock)
    with pytest.raises(ValueError, match="generation changed"):
        export_module._snapshot_annotations(ImageClassificationAnnotation.objects.all())


@pytest.mark.django_db(transaction=True)
def test_export_snapshot_locks_generation_until_transaction_finishes() -> None:
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL row locking is required")
    video = VideoFile.objects.create(
        center=Center.objects.create(name="locked-generation-center"),
        raw_video_hash="locked-generation-video",
    )
    frame = Frame.objects.create(video=video, frame_number=0, relative_path="frame.jpg")
    ImageClassificationAnnotation.objects.create(
        value=True,
        frame=frame,
        label=Label.objects.create(name="locked-generation-label"),
    )

    def attempt_publication_lock() -> bool:
        close_old_connections()
        try:
            with transaction.atomic():
                VideoFile.objects.select_for_update(nowait=True).get(pk=video.pk)
            return True
        except OperationalError:
            return False
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=1) as executor:
        with transaction.atomic():
            selected = export_module._snapshot_annotations(
                export_module._build_annotations_queryset().filter(frame__video=video)
            )
            assert len(selected) == 1
            assert executor.submit(attempt_publication_lock).result(timeout=5) is False
        assert executor.submit(attempt_publication_lock).result(timeout=5) is True


@pytest.mark.django_db
@pytest.mark.parametrize("output_format", ["csv", "json"])
def test_direct_export_reuses_selection_after_transcode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output_format: str,
) -> None:
    video = VideoFile.objects.create(
        center=Center.objects.create(name="direct-snapshot-center"),
        raw_video_hash="direct-snapshot-video",
    )
    frame = Frame.objects.create(video=video, frame_number=0, relative_path="frame.jpg")
    label = Label.objects.create(name="direct-snapshot-label")
    source = InformationSource.objects.create(name="direct-snapshot-source")
    selected = ImageClassificationAnnotation.objects.create(
        value=True,
        frame=frame,
        label=label,
        information_source=source,
        annotator="original",
    )

    def transcode_then_change_selection(
        annotations: list[ImageClassificationAnnotation],
        **kwargs: object,
    ) -> None:
        assert [item.pk for item in annotations] == [selected.pk]
        ImageClassificationAnnotation.objects.filter(pk=selected.pk).update(
            annotator="changed"
        )
        ImageClassificationAnnotation.objects.create(
            value=True, frame=frame, label=label, information_source=source
        )

    monkeypatch.setattr(
        export_module,
        "transcode_videos_for_annotations",
        transcode_then_change_selection,
    )
    writer = (
        export_module.export_frames_with_labels_to_json
        if output_format == "json"
        else export_module.export_frames_with_labels_to_csv
    )
    output = writer(
        tmp_path / f"annotations.{output_format}",
        video_id=video.pk,
        transcode_frames=True,
        use_export_flags=False,
        only_validated=False,
    )
    if output_format == "json":
        rows = json.loads(output.read_text())
    else:
        with output.open() as exported_file:
            rows = list(csv.DictReader(exported_file))
    assert len(rows) == 1
    assert rows[0]["annotator"] == "original"


def _line_up_row(
    *, frame_number: int, presentation_timestamp: int | None
) -> export_module.AnnotationRow:
    row = cast(
        export_module.AnnotationRow,
        {name: None for name in export_module.DEFAULT_FIELDNAMES},
    )
    row.update(
        annotation_id=frame_number + 100,
        video_id=7,
        label_id=3,
        information_source_id=4,
        frame_number=frame_number,
        presentation_timestamp=presentation_timestamp,
    )
    return row


def _line_up_segment() -> FrameAnnotationExportSegment:
    return FrameAnnotationExportSegment(
        segment_id=1,
        video_id=7,
        label_id=3,
        label_name="label",
        information_source_id=4,
        start_frame_number=10,
        end_frame_number=20,
        start_presentation_timestamp=30_000,
        end_presentation_timestamp=60_000,
        start_presentation_time_seconds=1 / 3,
        end_presentation_time_seconds=2 / 3,
        stream_time_base_num=1,
        stream_time_base_den=90_000,
    )


@pytest.mark.parametrize(
    ("frame_number", "presentation_timestamp"),
    [(10, 30_000), (15, 45_000), (20, 60_000)],
)
def test_rows_within_inclusive_segment_timestamps_line_up(
    frame_number: int, presentation_timestamp: int
) -> None:
    export_module._assert_rows_line_up_with_segments(
        [
            _line_up_row(
                frame_number=frame_number,
                presentation_timestamp=presentation_timestamp,
            )
        ],
        [_line_up_segment()],
        require_segment_membership=True,
    )


@pytest.mark.parametrize(
    ("frame_number", "presentation_timestamp", "message"),
    [
        (15, 29_999, "does not lie within segment"),
        (15, 60_001, "does not lie within segment"),
        (15, None, "does not lie within segment"),
        (21, 63_000, "outside every exported segment"),
    ],
)
def test_rows_that_do_not_line_up_fail_the_export(
    frame_number: int, presentation_timestamp: int | None, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        export_module._assert_rows_line_up_with_segments(
            [
                _line_up_row(
                    frame_number=frame_number,
                    presentation_timestamp=presentation_timestamp,
                )
            ],
            [_line_up_segment()],
            require_segment_membership=True,
        )


@pytest.mark.parametrize(
    ("video_names", "expected"),
    [
        (
            [(9, "room2.mp4"), (8, "room2.mp4")],
            {8: "room2.mp4", 9: "room2_video9.mp4"},
        ),
        (
            [(1, "room2_video3.mp4"), (2, "room2.mp4"), (3, "room2.mp4")],
            {1: "room2_video3.mp4", 2: "room2.mp4", 3: "room2_video3_2.mp4"},
        ),
        (
            [
                (1, "room2_video5.mp4"),
                (2, "room2_video5_2.mp4"),
                (3, "room2_video5_3.mp4"),
                (4, "room2.mp4"),
                (5, "room2.mp4"),
            ],
            {
                1: "room2_video5.mp4",
                2: "room2_video5_2.mp4",
                3: "room2_video5_3.mp4",
                4: "room2.mp4",
                5: "room2_video5_4.mp4",
            },
        ),
        (
            [(1, "room2.mp4"), (2, "room2.mp4"), (3, "room2_video2.mp4")],
            {1: "room2.mp4", 2: "room2_video2.mp4", 3: "room2_video2_video3.mp4"},
        ),
    ],
)
def test_duplicate_export_file_names_gain_video_suffix(
    video_names: list[tuple[int, str]], expected: dict[int, str]
) -> None:
    videos = [
        _VideoDouble(
            pk=pk,
            raw_video_hash=f"hash-{pk}",
            original_file_name=file_name,
            sensitive_meta=SensitiveMeta(
                patient_first_name="Max", patient_last_name="Mustermann"
            ),
        )
        for pk, file_name in video_names
    ]

    names = export_module._unique_export_file_names(cast(list[VideoFile], videos))

    assert names == expected
    assert len(set(names.values())) == len(videos)
    assert (
        export_module._unique_export_file_names(
            cast(list[VideoFile], list(reversed(videos)))
        )
        == expected
    )


@pytest.mark.django_db
def test_segment_boundary_without_presentation_timestamp_fails() -> None:
    timeline = VideoTimelineContract(
        fps_num=25,
        fps_den=1,
        duration_seconds=0.2,
        frame_count=5,
        time_base_num=1,
        time_base_den=90_000,
    )
    video = VideoFile.objects.create(
        center=Center.objects.create(name="segment-boundary-center"),
        raw_video_hash="segment-boundary-video",
        fps=timeline.fps,
        frame_count=timeline.frame_count,
        duration=timeline.duration_seconds,
        meta={
            "source_timeline": evidence_as_json(
                VideoSourceTimelineEvidence(
                    persisted_at=datetime.now(UTC),
                    source=VideoArtifactProbe(
                        codec_name="h264",
                        pixel_format="yuv420p",
                        width=64,
                        height=48,
                        size_bytes=1024,
                        timeline=timeline,
                    ),
                    timestamp_mapping="ffprobe_pts",
                )
            )
        },
    )
    Frame.objects.create(
        video=video,
        frame_number=0,
        relative_path="frame_0.jpg",
        timestamp=0.0,
        presentation_timestamp=0,
    )
    # The last included frame exists only as an unextracted placeholder.
    Frame.objects.create(video=video, frame_number=3, relative_path="frame_3.jpg")
    segment = LabelVideoSegment.objects.create(
        video_file=video,
        label=Label.objects.create(name="segment-boundary-label"),
        start_frame_number=0,
        end_frame_number=4,
    )

    with pytest.raises(ValueError, match="boundary frame 3 has no persisted"):
        export_module._manifest_segments(
            cast(list[export_module._LabelVideoSegmentExportModel], [segment])
        )


def _provenance_timeline(
    time_base: tuple[int, int], ticks: list[int], *, content_hash: str = "d" * 64
) -> VideoFrameTimeline:
    return VideoFrameTimeline(
        content_hash=content_hash,
        time_base_num=time_base[0],
        time_base_den=time_base[1],
        presentation_timestamps=ticks,
    )


@pytest.mark.parametrize(
    ("output", "indices", "expected"),
    [
        (_provenance_timeline((1, 600), [0, 40]), [0, 2], "identical_ticks"),
        (_provenance_timeline((1, 19_200), [0, 640]), [0, 1], "identical_instants"),
    ],
)
def test_manifest_states_recording_timestamp_identity(
    monkeypatch: pytest.MonkeyPatch,
    output: VideoFrameTimeline,
    indices: list[int],
    expected: str,
) -> None:
    bundle = VideoProvenanceBundle(
        transformations=[
            VideoFrameProvenancePayload(
                source=_provenance_timeline(
                    (1, 600), [0, 20, 40], content_hash="c" * 64
                ),
                output=output,
                source_frame_indices=indices,
            )
        ]
    )

    def resolved(_video: VideoFile) -> VideoProvenanceBundle:
        return bundle

    monkeypatch.setattr(export_module, "resolve_video_provenance", resolved)

    assert (
        export_module._recording_timestamp_identity(cast(VideoFile, None)) == expected
    )


def test_retimed_media_is_reported_not_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = VideoProvenanceBundle(
        transformations=[
            VideoFrameProvenancePayload(
                source=_provenance_timeline(
                    (1, 600), [0, 20, 40], content_hash="c" * 64
                ),
                # Frame removal closed the gap: the last frame moved from 40 to 20.
                output=_provenance_timeline((1, 600), [0, 20]),
                source_frame_indices=[0, 2],
            )
        ]
    )

    def resolved(_video: VideoFile) -> VideoProvenanceBundle:
        return bundle

    monkeypatch.setattr(export_module, "resolve_video_provenance", resolved)
    video = _VideoDouble(pk=31)

    assert export_module._recording_timestamp_identity(cast(VideoFile, video)) == (
        "changed"
    )


def test_missing_ancestry_is_reported_as_unverified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(_video: VideoFile) -> VideoProvenanceBundle:
        raise ValueError("Actual frame provenance is unavailable for this recording")

    monkeypatch.setattr(export_module, "resolve_video_provenance", unavailable)

    assert export_module._recording_timestamp_identity(cast(VideoFile, None)) == (
        "unverified"
    )


@pytest.mark.django_db
@pytest.mark.parametrize("collision", ["name", "table_alias", "manifest_alias"])
def test_export_rejects_manifest_collision_before_writing(
    collision: str, tmp_path: Path
) -> None:
    manifest = tmp_path / "export_manifest.json"
    table = tmp_path / "annotations.json"
    if collision == "name":
        table = manifest
    elif collision == "table_alias":
        table.symlink_to(manifest)
    else:
        manifest.symlink_to(table)
    table.write_text("existing export")

    with pytest.raises(ValueError, match="reserved export manifest"):
        export_module.AnnotationExporterClient().run_export(
            export_module.export_config(
                output_dir=tmp_path,
                output_path=table.name,
                export_profile="pts_dataset_v1",
                export_frames=False,
                all_centers=True,
            )
        )
    assert table.read_text() == "existing export"
    assert manifest.read_text() == "existing export"


def _accept_test_media(_video: object) -> None:
    pass


@pytest.mark.parametrize("strict", [True, False])
@pytest.mark.parametrize("failure", ["missing", "empty_path", "unsafe_path", "copy"])
def test_frame_asset_failure_obeys_strict_export_contract(
    strict: bool, failure: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = _VideoDouble(pk=72)
    source = tmp_path / "source.png"
    if failure != "missing":
        source.write_bytes(b"processed image")
    relative_path = {
        "empty_path": "",
        "unsafe_path": "../outside.png",
    }.get(failure, "frame.png")
    frame = _FrameDouble(
        pk=15,
        video=cast(VideoFile, video),
        relative_path=relative_path,
        file_path=str(source),
    )
    annotations = cast(
        list[ImageClassificationAnnotation], [_AnnotationDouble(frame=frame)]
    )
    monkeypatch.setattr(
        export_module, "assert_video_media_export_ready", _accept_test_media
    )

    def frame_source(_frame: object, **_kwargs: object) -> Path:
        return source

    monkeypatch.setattr(export_module, "_resolve_frame_source_path", frame_source)

    def refuse_copy(*, source: Path, destination: Path) -> None:
        raise OSError("output volume unavailable")

    if failure == "copy":
        monkeypatch.setattr(export_module, "atomic_copy_file", refuse_copy)
    with pytest.raises((ValueError, OSError)) if strict else nullcontext():
        count = export_module._export_frames_from_annotations(
            annotations,
            output_dir=tmp_path / "exported",
            use_frame_pk_paths=False,
            frame_ext="png",
            strict_media_validation=strict,
        )
        assert count == 0


@pytest.mark.parametrize("strict", [True, False])
@pytest.mark.parametrize("source_kind", ["local", "storage"])
def test_video_copy_failure_obeys_strict_export_contract(
    strict: bool, source_kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = _VideoDouble(pk=73, processed_file=_NamedFile("processed.mp4"))
    source = tmp_path / "processed.mp4"
    source.write_bytes(b"processed video")
    frame = _FrameDouble(
        pk=16,
        video=cast(VideoFile, video),
        relative_path="frame.png",
        file_path="frame.png",
    )
    annotations = cast(
        list[ImageClassificationAnnotation], [_AnnotationDouble(frame=frame)]
    )
    monkeypatch.setattr(
        export_module, "assert_video_media_export_ready", _accept_test_media
    )

    def video_source(_video: object) -> Path | None:
        return source if source_kind == "local" else None

    monkeypatch.setattr(
        export_module, "_resolve_processed_video_source_path", video_source
    )

    @contextmanager
    def stored_file(_field: object) -> Generator[Path]:
        yield source

    def refuse_copy(*, source: Path, destination: Path) -> None:
        raise OSError("output volume unavailable")

    monkeypatch.setattr(export_module, "ensure_local_file", stored_file)
    monkeypatch.setattr(export_module, "atomic_copy_file", refuse_copy)
    if strict:
        with pytest.raises(OSError, match="output volume unavailable"):
            export_module._export_videos_from_annotations(
                annotations,
                output_dir=tmp_path / "videos",
                strict_media_validation=True,
                video_file_names={video.pk: "processed.mp4"},
            )
    else:
        assert (
            export_module._export_videos_from_annotations(
                annotations,
                output_dir=tmp_path / "videos",
                strict_media_validation=False,
                video_file_names={video.pk: "processed.mp4"},
            )
            == 0
        )

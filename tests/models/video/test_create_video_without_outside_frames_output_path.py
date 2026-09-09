import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import NoReturn, Protocol, cast

import pytest

from endoreg_db.models import (
    Center,
    EndoscopyProcessor,
    Frame,
    ImageClassificationAnnotation,
    InformationSource,
    Label,
    LabelVideoSegment,
    VideoFile,
)
from endoreg_db.services.media_operation_gate import (
    MediaOperationDeferred,
    create_video_stream_lease,
)
from endoreg_db.utils.paths import data_paths, to_storage_relative


@dataclass(frozen=True)
class _BlackeningCall:
    input_path: Path
    output_path: Path
    intervals: list[tuple[int, int]]
    quality_mode: str
    force_cpu: bool


class _CenterRelation(Protocol):
    def add(self, *objs: Center | int) -> None: ...


def _add_center(processor: EndoscopyProcessor, center: Center) -> None:
    cast(_CenterRelation, processor.centers).add(center)


class _ProcessedFileContext:
    def __init__(self, processed_path: Path) -> None:
        self._processed_path = processed_path

    def __enter__(self) -> Path:
        return self._processed_path

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        return False


def _ensure_processed_file_context(
    processed_path: Path,
) -> Callable[[VideoFile], _ProcessedFileContext]:
    def ensure_local_processed_video_file(
        video_obj: VideoFile,
    ) -> _ProcessedFileContext:
        return _ProcessedFileContext(processed_path)

    return ensure_local_processed_video_file


def _constant_video_hash(hash_value: str) -> Callable[[Path], str]:
    def get_video_hash(path: Path) -> str:
        return hash_value

    return get_video_hash


def _sync_streamable_noop(
    video_obj: VideoFile,
    *,
    include_raw: bool = True,
    include_processed: bool = True,
    save: bool = True,
) -> list[str]:
    return []


def _write_filtered_video(output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"filtered-video")
    return output_path


def _fail_active_stream_save_local_file(
    *args: object,
    **kwargs: object,
) -> NoReturn:
    raise AssertionError("must not swap active stream artifact")


def _create_video(tmp_path: Path) -> tuple[VideoFile, Path]:
    center = Center.objects.create(
        name=f"outside-stream-center-{uuid.uuid4().hex[:8]}",
        display_name="Outside Stream Center",
    )
    processor = EndoscopyProcessor.objects.create(
        name=f"outside-stream-processor-{uuid.uuid4().hex[:8]}",
        image_width=1920,
        image_height=1080,
        endoscope_image_x=0,
        endoscope_image_y=0,
        endoscope_image_width=1920,
        endoscope_image_height=1080,
        examination_date_x=0,
        examination_date_y=0,
        examination_date_width=100,
        examination_date_height=50,
        examination_time_x=0,
        examination_time_y=0,
        examination_time_width=100,
        examination_time_height=50,
        patient_first_name_x=0,
        patient_first_name_y=0,
        patient_first_name_width=100,
        patient_first_name_height=50,
        patient_last_name_x=0,
        patient_last_name_y=0,
        patient_last_name_width=100,
        patient_last_name_height=50,
        patient_dob_x=0,
        patient_dob_y=0,
        patient_dob_width=100,
        patient_dob_height=50,
        endoscope_type_x=0,
        endoscope_type_y=0,
        endoscope_type_width=100,
        endoscope_type_height=50,
        endoscope_sn_x=0,
        endoscope_sn_y=0,
        endoscope_sn_width=100,
        endoscope_sn_height=50,
    )
    _add_center(processor, center)
    video = VideoFile.objects.create(
        center=center,
        processor=processor,
        video_hash=f"outside-stream-{uuid.uuid4().hex}",
        fps=25.0,
        width=1920,
        height=1080,
        processed_video_hash="old-hash",
    )
    processed_dir = tmp_path / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    processed_path = processed_dir / "input.mp4"
    processed_path.write_bytes(b"processed-input")
    video.processed_file.name = to_storage_relative(
        data_paths["anonym_video"] / f"{video.video_hash}_filtered.mp4"
    )
    video.save(update_fields=["processed_file", "processed_video_hash"])
    return video, processed_path


@pytest.mark.django_db
def test_create_video_without_outside_frames_uses_streamed_rebuild(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video, processed_path = _create_video(tmp_path)
    outside_label, _ = Label.objects.get_or_create(name="outside")
    LabelVideoSegment.objects.create(
        video_file=video,
        label=outside_label,
        start_frame_number=10,
        end_frame_number=20,
    )

    captured: list[_BlackeningCall] = []
    monkeypatch.setattr(
        "endoreg_db.services.video_post_validation_blackening.ensure_local_processed_video_file",
        _ensure_processed_file_context(processed_path),
    )

    def fake_blacken_video_frame_intervals(
        input_path: Path,
        output_path: Path,
        *,
        intervals: Iterable[tuple[int, int]],
        quality_mode: str = "balanced",
        force_cpu: bool = False,
    ) -> Path:
        captured.append(
            _BlackeningCall(
                input_path=input_path,
                output_path=output_path,
                intervals=list(intervals),
                quality_mode=quality_mode,
                force_cpu=force_cpu,
            )
        )
        return _write_filtered_video(output_path)

    monkeypatch.setattr(
        "endoreg_db.services.video_post_validation_blackening.blacken_video_frame_intervals",
        fake_blacken_video_frame_intervals,
    )
    monkeypatch.setattr(
        "endoreg_db.services.video_post_validation_blackening.get_video_hash",
        _constant_video_hash("new-processed-hash"),
    )
    streamable_sync: list[tuple[VideoFile, bool, bool, bool]] = []

    def fake_sync_video_streamable_artifacts(
        video_obj: VideoFile,
        *,
        include_raw: bool = True,
        include_processed: bool = True,
        save: bool = True,
    ) -> list[str]:
        streamable_sync.append((video_obj, include_raw, include_processed, save))
        return []

    monkeypatch.setattr(
        "endoreg_db.services.video_post_validation_blackening.sync_video_streamable_artifacts",
        fake_sync_video_streamable_artifacts,
    )

    ok = VideoFile.create_video_without_outside_frames(video)

    assert ok is True
    expected_output_path = (
        data_paths["transcoding"]
        / f"{video.video_hash}.outside_frame_blackening.staged.mp4"
    )
    assert len(captured) == 1
    assert captured[0].input_path == processed_path
    assert captured[0].output_path == expected_output_path
    assert captured[0].intervals == [(10, 20)]
    assert captured[0].quality_mode == "balanced"
    assert captured[0].force_cpu is False
    assert not expected_output_path.exists()

    video.refresh_from_db()
    assert video.processed_video_hash == "new-processed-hash"
    assert video.processed_file.name == to_storage_relative(
        data_paths["anonym_video"]
        / f"{video.video_hash}.post_validation.new-processed-hash.mp4"
    )
    assert len(streamable_sync) == 1


@pytest.mark.django_db
def test_create_video_without_outside_frames_defers_swap_when_stream_active(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video, processed_path = _create_video(tmp_path)
    original_processed_name = video.processed_file.name
    outside_label, _ = Label.objects.get_or_create(name="outside")
    LabelVideoSegment.objects.create(
        video_file=video,
        label=outside_label,
        start_frame_number=10,
        end_frame_number=20,
    )
    create_video_stream_lease(video, file_type="processed", ttl_seconds=120)

    monkeypatch.setattr(
        "endoreg_db.services.video_post_validation_blackening.ensure_local_processed_video_file",
        _ensure_processed_file_context(processed_path),
    )

    def fake_blacken_video_frame_intervals(
        input_path: Path,
        output_path: Path,
        *,
        intervals: Iterable[tuple[int, int]],
    ) -> Path:
        return _write_filtered_video(output_path)

    monkeypatch.setattr(
        "endoreg_db.services.video_post_validation_blackening.blacken_video_frame_intervals",
        fake_blacken_video_frame_intervals,
    )
    monkeypatch.setattr(
        "endoreg_db.services.video_post_validation_blackening.get_video_hash",
        _constant_video_hash("new-processed-hash"),
    )
    monkeypatch.setattr(
        "endoreg_db.services.video_post_validation_blackening.save_local_file",
        _fail_active_stream_save_local_file,
    )
    with pytest.raises(MediaOperationDeferred):
        VideoFile.create_video_without_outside_frames(video)

    video.refresh_from_db()
    assert video.processed_video_hash == "old-hash"
    assert video.processed_file.name == original_processed_name


@pytest.mark.django_db
def test_create_video_without_outside_frames_merges_adjacent_intervals_and_noops_when_empty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video, processed_path = _create_video(tmp_path)
    outside_label, _ = Label.objects.get_or_create(name="outside")
    LabelVideoSegment.objects.create(
        video_file=video,
        label=outside_label,
        start_frame_number=10,
        end_frame_number=20,
    )
    LabelVideoSegment.objects.create(
        video_file=video,
        label=outside_label,
        start_frame_number=20,
        end_frame_number=30,
    )
    LabelVideoSegment.objects.create(
        video_file=video,
        label=outside_label,
        start_frame_number=100,
        end_frame_number=110,
    )

    monkeypatch.setattr(
        "endoreg_db.services.video_post_validation_blackening.ensure_local_processed_video_file",
        _ensure_processed_file_context(processed_path),
    )

    calls: list[list[tuple[int, int]]] = []

    def fake_blacken_video_frame_intervals(
        input_path: Path,
        output_path: Path,
        *,
        intervals: Iterable[tuple[int, int]],
        quality_mode: str = "balanced",
        force_cpu: bool = False,
    ) -> Path:
        calls.append(list(intervals))
        return _write_filtered_video(output_path)

    monkeypatch.setattr(
        "endoreg_db.services.video_post_validation_blackening.blacken_video_frame_intervals",
        fake_blacken_video_frame_intervals,
    )
    monkeypatch.setattr(
        "endoreg_db.services.video_post_validation_blackening.get_video_hash",
        _constant_video_hash("merged-hash"),
    )
    monkeypatch.setattr(
        "endoreg_db.services.video_post_validation_blackening.sync_video_streamable_artifacts",
        _sync_streamable_noop,
    )

    assert VideoFile.create_video_without_outside_frames(video) is True
    assert calls == [[(10, 30), (100, 110)]]

    LabelVideoSegment.objects.all().delete()
    calls.clear()
    assert VideoFile.create_video_without_outside_frames(video) is True
    assert calls == []


@pytest.mark.django_db
def test_create_video_without_outside_frames_uses_supplied_intervals(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video, processed_path = _create_video(tmp_path)

    monkeypatch.setattr(
        "endoreg_db.services.video_post_validation_blackening.ensure_local_processed_video_file",
        _ensure_processed_file_context(processed_path),
    )

    calls: list[list[tuple[int, int]]] = []

    def fake_blacken_video_frame_intervals(
        input_path: Path,
        output_path: Path,
        *,
        intervals: Iterable[tuple[int, int]],
        quality_mode: str = "balanced",
        force_cpu: bool = False,
    ) -> Path:
        calls.append(list(intervals))
        return _write_filtered_video(output_path)

    monkeypatch.setattr(
        "endoreg_db.services.video_post_validation_blackening.blacken_video_frame_intervals",
        fake_blacken_video_frame_intervals,
    )
    monkeypatch.setattr(
        "endoreg_db.services.video_post_validation_blackening.get_video_hash",
        _constant_video_hash("supplied-interval-hash"),
    )
    monkeypatch.setattr(
        "endoreg_db.services.video_post_validation_blackening.sync_video_streamable_artifacts",
        _sync_streamable_noop,
    )

    assert (
        VideoFile.create_video_without_outside_frames(
            video,
            outside_intervals=[(5, 6), (10, 12)],
        )
        is True
    )
    assert calls == [[(5, 6), (10, 12)]]


@pytest.mark.django_db
def test_create_video_without_outside_frames_includes_frame_level_outside_annotations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video, processed_path = _create_video(tmp_path)
    outside_label, _ = Label.objects.get_or_create(name="outside")
    source, _ = InformationSource.objects.get_or_create(name="manual_annotation")
    outside_frame = Frame.objects.create(
        video=video,
        frame_number=44,
        relative_path="frame_0000044.jpg",
        is_extracted=False,
    )
    ImageClassificationAnnotation.objects.create(
        frame=outside_frame,
        label=outside_label,
        information_source=source,
        value=True,
    )

    monkeypatch.setattr(
        "endoreg_db.services.video_post_validation_blackening.ensure_local_processed_video_file",
        _ensure_processed_file_context(processed_path),
    )

    calls: list[list[tuple[int, int]]] = []

    def fake_blacken_video_frame_intervals(
        input_path: Path,
        output_path: Path,
        *,
        intervals: Iterable[tuple[int, int]],
        quality_mode: str = "balanced",
        force_cpu: bool = False,
    ) -> Path:
        calls.append(list(intervals))
        return _write_filtered_video(output_path)

    monkeypatch.setattr(
        "endoreg_db.services.video_post_validation_blackening.blacken_video_frame_intervals",
        fake_blacken_video_frame_intervals,
    )
    monkeypatch.setattr(
        "endoreg_db.services.video_post_validation_blackening.get_video_hash",
        _constant_video_hash("annotation-hash"),
    )
    monkeypatch.setattr(
        "endoreg_db.services.video_post_validation_blackening.sync_video_streamable_artifacts",
        _sync_streamable_noop,
    )

    assert VideoFile.create_video_without_outside_frames(video) is True
    assert calls == [[(44, 45)]]

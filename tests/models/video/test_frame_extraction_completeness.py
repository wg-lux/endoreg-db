from __future__ import annotations

from contextlib import nullcontext
import importlib
from pathlib import Path
import uuid

import pytest

from endoreg_db.models import Center, Frame, VideoFile
from endoreg_db.models.state.anonymization import AnonymizationState
from endoreg_db.services.anonymization import AnonymizationService
from endoreg_db.services.media_integrity import mark_video_integrity_lost

extract_frames_module = importlib.import_module(
    "endoreg_db.services.video_files._frames._extract_frames"
)
frame_range_module = importlib.import_module(
    "endoreg_db.services.video_files._frames._manage_frame_range"
)


def _video(tmp_path: Path, *, frame_count: int = 3) -> VideoFile:
    center = Center.objects.create(name=f"frame-complete-{uuid.uuid4().hex[:8]}")
    frame_dir = tmp_path / f"frames-{uuid.uuid4().hex[:8]}"
    video = VideoFile.objects.create(
        center=center,
        video_hash=f"frame-complete-{uuid.uuid4().hex}",
        frame_count=frame_count,
        frame_dir=str(frame_dir),
    )
    video.initialize_frames()
    return video


@pytest.mark.django_db
def test_full_extraction_reextracts_when_only_partial_files_exist(
    monkeypatch, tmp_path
):
    video = _video(tmp_path, frame_count=3)
    frame_dir = video.get_frame_dir_path()
    assert frame_dir is not None
    frame_dir.mkdir(parents=True, exist_ok=True)
    (frame_dir / "frame_0000001.jpg").write_bytes(b"partial")

    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"video")
    calls: list[Path] = []

    def fake_extract_frames(source, output_dir, **kwargs):
        calls.append(Path(source))
        paths = []
        for frame_number in range(3):
            path = output_dir / f"frame_{frame_number:07d}.jpg"
            path.write_bytes(f"frame-{frame_number}".encode())
            paths.append(path)
        return paths

    monkeypatch.setattr(VideoFile, "has_raw", property(lambda _self: True))
    monkeypatch.setattr(
        extract_frames_module,
        "_video_source_context",
        lambda _video, *, from_processed: nullcontext(source_path),
    )
    monkeypatch.setattr(
        extract_frames_module,
        "ffmpeg_extract_frames",
        fake_extract_frames,
    )

    assert video.extract_frames(overwrite=False) is True
    assert calls == [source_path]

    state = video.get_or_create_state()
    state.refresh_from_db()
    assert state.frames_extracted is True
    assert state.frames_initialized is True
    assert state.frame_count == 3
    assert sorted(path.name for path in frame_dir.glob("frame_*.jpg")) == [
        "frame_0000000.jpg",
        "frame_0000001.jpg",
        "frame_0000002.jpg",
    ]
    assert list(
        Frame.objects.filter(video=video)
        .order_by("frame_number")
        .values_list("frame_number", "relative_path", "is_extracted")
    ) == [
        (0, "frame_0000000.jpg", True),
        (1, "frame_0000001.jpg", True),
        (2, "frame_0000002.jpg", True),
    ]


@pytest.mark.django_db
def test_full_extraction_error_preserves_existing_cache_until_replacement_verified(
    monkeypatch,
    tmp_path,
):
    video = _video(tmp_path, frame_count=3)
    frame_dir = video.get_frame_dir_path()
    assert frame_dir is not None
    frame_dir.mkdir(parents=True, exist_ok=True)
    sentinel = frame_dir / "frame_0000001.jpg"
    sentinel.write_bytes(b"legacy-cache")

    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"video")

    def fake_extract_frames(*_args, **_kwargs):
        raise RuntimeError("ffmpeg failed before replacement was verified")

    monkeypatch.setattr(VideoFile, "has_raw", property(lambda _self: True))
    monkeypatch.setattr(
        extract_frames_module,
        "_video_source_context",
        lambda _video, *, from_processed: nullcontext(source_path),
    )
    monkeypatch.setattr(
        extract_frames_module,
        "ffmpeg_extract_frames",
        fake_extract_frames,
    )

    # Existing cache files may still carry review value. Full extraction should
    # build a replacement elsewhere and only swap it in after verification.
    with pytest.raises(RuntimeError):
        video.extract_frames(overwrite=False)

    assert sentinel.exists()
    assert sentinel.read_bytes() == b"legacy-cache"


@pytest.mark.django_db
def test_full_extraction_skips_only_when_expected_files_are_complete(
    monkeypatch,
    tmp_path,
):
    video = _video(tmp_path, frame_count=2)
    frame_dir = video.get_frame_dir_path()
    assert frame_dir is not None
    frame_dir.mkdir(parents=True, exist_ok=True)
    for frame_number in range(2):
        (frame_dir / f"frame_{frame_number:07d}.jpg").write_bytes(b"frame")

    state = video.get_or_create_state()
    state.frames_extracted = False
    state.save(update_fields=["frames_extracted"])
    Frame.objects.filter(video=video).update(is_extracted=False)

    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"video")

    monkeypatch.setattr(VideoFile, "has_raw", property(lambda _self: True))
    monkeypatch.setattr(
        extract_frames_module,
        "_video_source_context",
        lambda _video, *, from_processed: nullcontext(source_path),
    )
    monkeypatch.setattr(
        extract_frames_module,
        "ffmpeg_extract_frames",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("complete extraction should have been reused")
        ),
    )

    assert video.extract_frames(overwrite=False) is True
    state.refresh_from_db()
    assert state.frames_extracted is True
    assert Frame.objects.filter(video=video, is_extracted=True).count() == 2


@pytest.mark.django_db
def test_full_extraction_rejects_extractor_returned_paths_missing_on_disk(
    monkeypatch,
    tmp_path,
):
    video = _video(tmp_path, frame_count=3)

    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"video")

    def fake_extract_frames(_source, output_dir, **_kwargs):
        paths = []
        for frame_number in range(3):
            path = output_dir / f"frame_{frame_number:07d}.jpg"
            if frame_number != 2:
                path.write_bytes(f"frame-{frame_number}".encode())
            paths.append(path)
        return paths

    monkeypatch.setattr(VideoFile, "has_raw", property(lambda _self: True))
    monkeypatch.setattr(
        extract_frames_module,
        "_video_source_context",
        lambda _video, *, from_processed: nullcontext(source_path),
    )
    monkeypatch.setattr(
        extract_frames_module,
        "ffmpeg_extract_frames",
        fake_extract_frames,
    )

    with pytest.raises(RuntimeError):
        video.extract_frames(overwrite=False)

    state = video.get_or_create_state()
    state.refresh_from_db()
    assert state.frames_extracted is False
    assert Frame.objects.filter(video=video, is_extracted=True).count() == 0


@pytest.mark.django_db
def test_full_extraction_fast_path_repairs_stale_db_from_complete_disk(
    monkeypatch,
    tmp_path,
):
    video = _video(tmp_path, frame_count=3)
    frame_dir = video.get_frame_dir_path()
    assert frame_dir is not None
    frame_dir.mkdir(parents=True, exist_ok=True)
    for frame_number in range(3):
        (frame_dir / f"frame_{frame_number:07d}.jpg").write_bytes(b"frame")

    state = video.get_or_create_state()
    state.frames_extracted = True
    state.save(update_fields=["frames_extracted"])
    Frame.objects.filter(video=video, frame_number=1).update(
        relative_path="frame_0000001.jpg",
        is_extracted=False,
    )
    Frame.objects.filter(video=video, frame_number=2).update(
        relative_path="legacy-name.jpg",
        is_extracted=True,
    )

    monkeypatch.setattr(
        extract_frames_module,
        "ffmpeg_extract_frames",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("complete disk cache should repair DB without FFmpeg")
        ),
    )

    assert video.extract_frames(overwrite=False) is True

    state.refresh_from_db()
    assert state.frames_extracted is True
    assert state.frame_count == 3
    assert list(
        Frame.objects.filter(video=video)
        .order_by("frame_number")
        .values_list("frame_number", "relative_path", "is_extracted")
    ) == [
        (0, "frame_0000000.jpg", True),
        (1, "frame_0000001.jpg", True),
        (2, "frame_0000002.jpg", True),
    ]


@pytest.mark.django_db
def test_full_extraction_corrects_single_trailing_frame_from_manifest(
    monkeypatch,
    tmp_path,
):
    video = _video(tmp_path, frame_count=2)

    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"video")

    def fake_extract_frames(_source, output_dir, **_kwargs):
        paths = []
        for frame_number in range(3):
            path = output_dir / f"frame_{frame_number:07d}.jpg"
            path.write_bytes(f"frame-{frame_number}".encode())
            paths.append(path)
        return paths

    monkeypatch.setattr(VideoFile, "has_raw", property(lambda _self: True))
    monkeypatch.setattr(
        extract_frames_module,
        "_video_source_context",
        lambda _video, *, from_processed: nullcontext(source_path),
    )
    monkeypatch.setattr(
        extract_frames_module,
        "ffmpeg_extract_frames",
        fake_extract_frames,
    )

    assert video.extract_frames(overwrite=False) is True
    video.refresh_from_db()
    state = video.get_or_create_state()
    state.refresh_from_db()
    assert video.frame_count == 3
    assert state.frame_count == 3
    assert Frame.objects.filter(video=video, is_extracted=True).count() == 3


@pytest.mark.django_db
def test_range_extraction_recreates_stale_extracted_flag_file(monkeypatch, tmp_path):
    video = _video(tmp_path, frame_count=10)
    frame_dir = video.get_frame_dir_path()
    assert frame_dir is not None
    frame = Frame.objects.get(video=video, frame_number=7)
    Frame.objects.filter(pk=frame.pk).update(is_extracted=True)

    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"video")
    calls: list[tuple[int, int]] = []

    def fake_extract_frame_range(
        source,
        output_dir,
        start_frame,
        end_frame,
        **kwargs,
    ):
        calls.append((start_frame, end_frame))
        path = output_dir / "frame_0000007.jpg"
        path.write_bytes(b"frame-seven")
        return [path]

    monkeypatch.setattr(VideoFile, "has_raw", property(lambda _self: True))
    monkeypatch.setattr(
        frame_range_module,
        "_raw_video_source_context",
        lambda _video: nullcontext(source_path),
    )
    monkeypatch.setattr(
        frame_range_module,
        "ffmpeg_extract_frame_range",
        fake_extract_frame_range,
    )

    assert video.extract_specific_frame_range(7, 8, overwrite=False) is True
    assert calls == [(7, 8)]

    frame.refresh_from_db()
    assert frame.relative_path == "frame_0000007.jpg"
    assert frame.is_extracted is True
    assert frame.file_path.exists()


@pytest.mark.django_db
def test_anonymize_refuses_video_marked_integrity_lost(tmp_path):
    video = _video(tmp_path, frame_count=3)

    mark_video_integrity_lost(video, "frame cache remains invalid")

    video.refresh_from_db()
    state = video.get_or_create_state()
    state.refresh_from_db()
    assert state.processing_error is True
    assert state.anonymization_status == AnonymizationState.FAILED
    status_payload = AnonymizationService.get_status(video.pk, kind="video")
    assert status_payload is not None
    assert status_payload["anonymization_status"] == AnonymizationState.FAILED.value
    assert status_payload["integrity_status"] == "lost"

    with pytest.raises(ValueError, match="failed/lost"):
        video.anonymize(delete_original_raw=True)

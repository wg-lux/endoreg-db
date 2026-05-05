from __future__ import annotations

import uuid
from pathlib import Path

import pytest

import endoreg_db.services.media_integrity as media_integrity
from endoreg_db.models import (
    Center,
    Frame,
    ImageClassificationAnnotation,
    Label,
    VideoFile,
)
from endoreg_db.services.media_integrity import reconcile_media_integrity
from endoreg_db.utils.file_operations import atomic_write_file, ensure_directory


pytestmark = pytest.mark.django_db


def _video_with_initialized_frames(
    tmp_path: Path,
    *,
    frame_count: int = 3,
    materialize_cache: bool = False,
) -> VideoFile:
    center = Center.objects.create(
        name=f"media-integrity-center-{uuid.uuid4().hex[:8]}",
        display_name="Media Integrity Center",
    )
    frame_dir = tmp_path / f"frames-{uuid.uuid4().hex[:8]}"
    if materialize_cache:
        frame_dir.mkdir(parents=True, exist_ok=True)

    video = VideoFile.objects.create(
        center=center,
        video_hash=f"media-integrity-{uuid.uuid4().hex}",
        frame_count=frame_count,
        frame_dir=str(frame_dir),
    )
    video.initialize_frames()
    Frame.objects.filter(video=video).update(is_extracted=True)

    state = video.get_or_create_state()
    state.frames_initialized = True
    state.frame_count = frame_count
    state.frames_extracted = True
    state.save(
        update_fields=[
            "frames_initialized",
            "frame_count",
            "frames_extracted",
        ]
    )
    return video


def _write_test_file(
    path: Path, payload: bytes, *, file_mode: int | None = None
) -> Path:
    return atomic_write_file(destination=path, content=[payload], file_mode=file_mode)


def test_reconcile_frames_treats_missing_cache_as_cache_miss_not_corruption(
    tmp_path: Path,
):
    video = _video_with_initialized_frames(tmp_path, frame_count=3)
    frame_dir = video.get_frame_dir_path()
    assert frame_dir is not None
    assert not frame_dir.exists()

    summary = reconcile_media_integrity(
        dry_run=True,
        video_ids=[video.pk],
        check_frames=True,
        repair_frames=False,
    )

    video.refresh_from_db()
    state = video.get_or_create_state()
    state.refresh_from_db()

    # Missing frame directories are expected cache misses under the current
    # storage policy. Reconciliation must not downgrade canonical DB state.
    assert summary.checked_videos == 1
    assert summary.frame_cache_missing == 1
    assert summary.frame_cache_corrupt == 0
    assert summary.lost_records == 0
    assert state.frames_extracted is True
    assert Frame.objects.filter(video=video, is_extracted=True).count() == 3


def test_dry_run_does_not_create_missing_stable_frame(
    tmp_path: Path,
):
    video = _video_with_initialized_frames(
        tmp_path,
        frame_count=3,
        materialize_cache=True,
    )
    frame_dir = video.get_frame_dir_path()
    assert frame_dir is not None
    for frame_number in (1, 2):
        _write_test_file(
            frame_dir / f"frame_{frame_number:07d}.jpg",
            f"frame-{frame_number}".encode(),
        )

    summary = reconcile_media_integrity(
        dry_run=True,
        video_ids=[video.pk],
        check_frames=True,
        repair_frames=True,
    )

    assert summary.frame_cache_partial == 1
    assert summary.repaired_frames == 0
    assert not (frame_dir / "frame_0000000.jpg").exists()


def test_targeted_frame_zero_fix_uses_staged_output(monkeypatch, tmp_path: Path):
    video = _video_with_initialized_frames(
        tmp_path,
        frame_count=3,
        materialize_cache=True,
    )
    frame_dir = video.get_frame_dir_path()
    assert frame_dir is not None
    for frame_number in (1, 2):
        _write_test_file(
            frame_dir / f"frame_{frame_number:07d}.jpg",
            f"frame-{frame_number}".encode(),
        )

    seen_output_dirs: list[Path] = []

    def fake_extract_range(video_arg, *, output_dir, start_frame, end_frame, **_kwargs):
        assert video_arg == video
        assert start_frame == 0
        assert end_frame == 1
        output_dir = Path(output_dir)
        seen_output_dirs.append(output_dir)
        ensure_directory(output_dir)
        path = output_dir / "frame_0000000.jpg"
        _write_test_file(path, b"frame-zero")
        return [path]

    monkeypatch.setattr(
        media_integrity,
        "extract_frame_range_to_directory",
        fake_extract_range,
    )

    summary = reconcile_media_integrity(
        dry_run=False,
        video_ids=[video.pk],
        check_frames=True,
        repair_frames=True,
        repair_frame_numbers=[0],
    )

    assert summary.frame_cache_partial == 1
    assert summary.repaired_frames == 1
    assert (frame_dir / "frame_0000000.jpg").read_bytes() == b"frame-zero"
    assert seen_output_dirs
    assert seen_output_dirs[0] != frame_dir
    assert seen_output_dirs[0].name.startswith(".extracting_")
    assert not seen_output_dirs[0].exists()
    frame_zero = Frame.objects.get(video=video, frame_number=0)
    assert frame_zero.relative_path == "frame_0000000.jpg"
    assert frame_zero.is_extracted is True


def test_shifted_cache_with_annotations_is_reported_only(
    tmp_path: Path,
):
    video = _video_with_initialized_frames(
        tmp_path,
        frame_count=3,
        materialize_cache=True,
    )
    frame_dir = video.get_frame_dir_path()
    assert frame_dir is not None
    for frame_number in (1, 2, 3):
        _write_test_file(
            frame_dir / f"frame_{frame_number:07d}.jpg",
            f"legacy-frame-{frame_number}".encode(),
        )

    label = Label.objects.create(name=f"manual-label-{uuid.uuid4().hex[:8]}")
    ImageClassificationAnnotation.objects.create(
        frame=Frame.objects.get(video=video, frame_number=1),
        label=label,
        value=True,
        annotator="manual-reviewer",
    )

    summary = reconcile_media_integrity(
        dry_run=False,
        video_ids=[video.pk],
        check_frames=True,
        repair_frames=True,
    )

    # A shifted non-empty cache with dependent annotations is evidence to review,
    # not permission to rewrite visual content under stable Frame rows.
    assert summary.frame_cache_shifted == 1
    assert summary.frame_cache_manual_review_required == 1
    assert summary.repaired_frames == 0
    assert not (frame_dir / "frame_0000000.jpg").exists()
    assert (frame_dir / "frame_0000003.jpg").exists()


def test_ffmpeg_report_records_defaulted_fps_source(tmp_path: Path):
    video = _video_with_initialized_frames(tmp_path, frame_count=3)
    video.fps = None
    video.save(update_fields=["fps"])

    summary = reconcile_media_integrity(
        dry_run=True,
        video_ids=[video.pk],
        check_ffmpeg_meta=True,
    )

    report = summary.video_reports[0]["ffmpeg_metadata"]
    assert report["fps_provenance"] == "fps_defaulted"
    assert report["action"] == "fps_defaulted"
    assert report["default_fps"] == 50.0


def test_ffmpeg_report_records_db_fps_source(tmp_path: Path):
    video = _video_with_initialized_frames(tmp_path, frame_count=3)
    video.fps = 42.0
    video.save(update_fields=["fps"])

    summary = reconcile_media_integrity(
        dry_run=True,
        video_ids=[video.pk],
        check_ffmpeg_meta=True,
    )

    report = summary.video_reports[0]["ffmpeg_metadata"]
    assert report["fps_provenance"] == "fps_from_existing_db"
    assert report["existing_video_fps"] == 42.0
    assert report["action"] == "probe_unavailable"


def test_ffmpeg_report_uses_streamable_fallback_source(monkeypatch, tmp_path: Path):
    video = _video_with_initialized_frames(tmp_path, frame_count=3)
    streamable_path = tmp_path / "streamable" / "processed" / "fallback.mp4"
    _write_test_file(streamable_path, b"video")
    video.processed_streamable_relative_path = "streamable/processed/fallback.mp4"
    video.save(update_fields=["processed_streamable_relative_path"])

    probe_data = {
        "streams": [
            {
                "codec_type": "video",
                "avg_frame_rate": "50/1",
                "r_frame_rate": "50/1",
            }
        ]
    }

    monkeypatch.setattr(media_integrity, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(
        media_integrity,
        "_probe_video_path",
        lambda path: (True, probe_data, ""),
    )

    summary = reconcile_media_integrity(
        dry_run=True,
        video_ids=[video.pk],
        check_ffmpeg_meta=True,
    )

    report = summary.video_reports[0]["ffmpeg_metadata"]
    assert report["source"] == "processed_streamable_fallback"
    assert report["fps_provenance"] == "fps_verified_by_ffprobe"
    assert report["probed_fps"] == 50.0
    assert report["action"] == "would_backfill_ffmpeg_meta"


def test_corrupt_streamable_with_valid_canonical_is_rebuild_only(
    monkeypatch, tmp_path: Path
):
    video = _video_with_initialized_frames(tmp_path, frame_count=3)
    streamable_path = tmp_path / "streamable" / "processed" / "fallback.mp4"
    _write_test_file(
        streamable_path,
        b"corrupt-streamable",
        file_mode=media_integrity.STREAMABLE_FILE_MODE,
    )
    video.processed_file.name = "processed/missing.mp4"
    video.processed_streamable_relative_path = "streamable/processed/fallback.mp4"
    video.save(update_fields=["processed_file", "processed_streamable_relative_path"])

    monkeypatch.setattr(media_integrity, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(
        media_integrity,
        "_probe_video_path",
        lambda path: (False, {"streams": []}, "corrupt streamable"),
    )
    monkeypatch.setattr(
        media_integrity,
        "_verify_canonical_probe",
        lambda video_arg, *, processed: (True, ""),
    )

    called: list[dict[str, bool]] = []

    def fake_sync(video_arg, *, include_raw, include_processed, save):
        called.append(
            {
                "include_raw": include_raw,
                "include_processed": include_processed,
                "save": save,
            }
        )
        return []

    monkeypatch.setattr(
        media_integrity,
        "sync_video_streamable_artifacts",
        fake_sync,
    )

    summary = reconcile_media_integrity(
        dry_run=True,
        video_ids=[video.pk],
        check_streamable_probe=True,
    )

    assert called == []
    assert summary.streamable_artifacts_checked == 1
    assert summary.streamable_artifacts_repaired == 1
    artifact = summary.video_reports[0]["streamable_probe"]["artifacts"][0]
    assert artifact["kind"] == "processed"
    assert artifact["probe_ok"] is False
    assert artifact["canonical_probe_ok"] is True
    assert artifact["action"] == "would_rebuild_streamable"

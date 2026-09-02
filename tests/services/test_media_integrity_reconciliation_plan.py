from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pytest import MonkeyPatch

import endoreg_db.services.media_integrity as media_integrity
from endoreg_db.models import (
    AIDataSet,
    AIModelTrainingRun,
    Center,
    Frame,
    ImageClassificationAnnotation,
    Label,
    LabelVideoSegment,
    UploadJob,
    VideoFile,
)
from endoreg_db.services.media_integrity import (
    MediaIntegrityOptions,
    reconcile_media_integrity,
    reconcile_upload_job_integrity,
    reconcile_video_integrity,
)
from endoreg_db.utils.file_operations import (
    atomic_write_file,
    ensure_directory,
)


pytestmark = pytest.mark.django_db


def test_missing_upload_source_uses_integrity_lifecycle_event(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    upload_job = cast(
        UploadJob,
        SimpleNamespace(
            pk="missing-upload-job",
            file=SimpleNamespace(name="uploads/missing.pdf"),
            source_file_persisted=True,
            content_hash="a" * 64,
        ),
    )
    transitions: list[tuple[UploadJob, str]] = []

    def record_integrity_lost(job: UploadJob, detail: str) -> None:
        transitions.append((job, detail))

    monkeypatch.setattr(media_integrity, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(
        media_integrity,
        "mark_upload_job_integrity_lost",
        record_integrity_lost,
    )

    repaired, lost, report = reconcile_upload_job_integrity(upload_job)

    assert repaired == 0
    assert lost == 1
    assert report["action"] == "lost"
    assert transitions == [(upload_job, report["detail"])]


def test_blank_persisted_upload_source_is_lost(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Arrange
    upload_job = cast(
        UploadJob,
        SimpleNamespace(
            pk="blank-upload-job",
            file=SimpleNamespace(name=""),
            source_file_persisted=True,
            content_hash="b" * 64,
        ),
    )
    transitions: list[str] = []

    def record_blank_source_loss(_job: UploadJob, detail: str) -> None:
        transitions.append(detail)

    monkeypatch.setattr(media_integrity, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(
        media_integrity,
        "mark_upload_job_integrity_lost",
        record_blank_source_loss,
    )

    # Act
    repaired, lost, report = reconcile_upload_job_integrity(upload_job)

    # Assert
    assert repaired == 0
    assert lost == 1
    assert report["action"] == "lost"
    assert transitions == [report["detail"]]
    assert "storage reference" in transitions[0]


def _video_with_successful_lifecycle_state() -> VideoFile:
    center = Center.objects.create(
        name=f"successful-video-center-{uuid.uuid4().hex[:8]}",
        display_name="Successful Video Center",
    )
    video = VideoFile.objects.create(
        center=center,
        video_hash=f"successful-video-{uuid.uuid4().hex}",
        processed_file="",
    )
    state = video.get_or_create_state()
    state.sensitive_meta_processed = True
    state.anonymized = True
    state.anonymization_validated = True
    state.outside_segments_removed = True
    state.save(
        update_fields=[
            "sensitive_meta_processed",
            "anonymized",
            "anonymization_validated",
            "outside_segments_removed",
        ]
    )
    return video


def test_successful_video_without_processed_file_is_lost() -> None:
    # Arrange
    video = _video_with_successful_lifecycle_state()

    # Act
    repaired, lost, report = reconcile_video_integrity(
        video,
        options=MediaIntegrityOptions(dry_run=True),
    )

    # Assert
    assert repaired == 0
    assert lost == 1
    assert report["status"] == "lost"


def test_processing_failure_without_integrity_evidence_is_not_lost() -> None:
    # Arrange
    center = Center.objects.create(
        name=f"failed-video-center-{uuid.uuid4().hex[:8]}",
        display_name="Failed Video Center",
    )
    video = VideoFile.objects.create(
        center=center,
        video_hash=f"failed-video-{uuid.uuid4().hex}",
    )
    state = video.get_or_create_state()
    state.processing_error = True
    state.save(update_fields=["processing_error"])

    # Act
    _repaired, lost, report = reconcile_video_integrity(
        video,
        options=MediaIntegrityOptions(dry_run=True),
    )

    # Assert
    assert lost == 0
    assert report.get("status") != "lost"


def test_preexisting_integrity_loss_is_counted_on_every_inventory() -> None:
    # Arrange
    video = _video_with_successful_lifecycle_state()
    video.meta = {
        "integrity_status": "lost",
        "integrity_error": "processed generation disappeared",
    }
    video.save(update_fields=["meta"])
    state = video.get_or_create_state()
    state.processing_error = True
    state.processing_started = False
    state.ready_for_export = False
    state.processed_file_sha256 = ""
    state.save()

    # Act
    _repaired, lost, report = reconcile_video_integrity(
        video,
        options=MediaIntegrityOptions(dry_run=True),
    )

    # Assert
    assert lost == 1
    assert report["status"] == "lost"


def test_video_integrity_loss_propagates_to_dependent_training_ledger() -> None:
    # Arrange
    video = _video_with_successful_lifecycle_state()
    segment = LabelVideoSegment.objects.create(
        video_file=video,
        start_frame_number=0,
        end_frame_number=1,
    )
    dataset = AIDataSet.objects.create(name="integrity-dependent-training")
    dataset.video_annotations.add(segment)
    run = AIModelTrainingRun.objects.create(
        dataset=dataset,
        backbone_name="test-backbone",
        feature_mode="test-features",
        status=AIModelTrainingRun.STATUS_COMPLETED,
    )

    # Act
    media_integrity.mark_video_integrity_lost(
        video,
        "Canonical processed video is missing.",
    )

    # Assert
    run.refresh_from_db()
    assert run.status == AIModelTrainingRun.STATUS_LOST
    assert "training input video artifact was lost" in run.error


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


def test_targeted_frame_zero_fix_uses_staged_output(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
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

    def fake_extract_range(
        video_arg: VideoFile,
        *,
        output_dir: Path | str,
        start_frame: int,
        end_frame: int,
        **_kwargs: Any,
    ) -> list[Path]:
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


def test_shifted_cache_without_annotations_is_replaced_atomically(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
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
    frame_one = Frame.objects.get(video=video, frame_number=1)
    frame_one.timestamp = 1.25
    frame_one.presentation_timestamp = 125
    frame_one.save(update_fields=["timestamp", "presentation_timestamp"])

    def fake_extract_full_frame_set(
        video_arg: VideoFile,
        *,
        output_dir: Path,
        ext: str,
        **_kwargs: Any,
    ) -> list[Path]:
        assert video_arg == video
        ensure_directory(output_dir)
        extracted_paths: list[Path] = []
        for frame_number in range(3):
            extracted_paths.append(
                _write_test_file(
                    output_dir / f"frame_{frame_number:07d}.{ext}",
                    f"replacement-{frame_number}".encode(),
                )
            )
        return extracted_paths

    monkeypatch.setattr(
        media_integrity,
        "extract_full_frame_set_to_directory",
        fake_extract_full_frame_set,
    )

    summary = reconcile_media_integrity(
        dry_run=False,
        video_ids=[video.pk],
        check_frames=True,
        repair_frames=True,
    )

    assert summary.frame_cache_shifted == 1
    assert summary.repaired_frames == 3
    assert sorted(path.name for path in frame_dir.glob("frame_*.jpg")) == [
        "frame_0000000.jpg",
        "frame_0000001.jpg",
        "frame_0000002.jpg",
    ]
    frame_one.refresh_from_db()
    assert frame_one.timestamp == 1.25
    assert frame_one.presentation_timestamp == 125
    assert frame_one.is_extracted is True


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


def test_ffmpeg_report_does_not_use_streamable_fallback_source(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    video = _video_with_initialized_frames(tmp_path, frame_count=3)
    streamable_path = tmp_path / "streamable" / "processed" / "fallback.mp4"
    _write_test_file(streamable_path, b"video")
    video.processed_streamable_relative_path = "streamable/processed/fallback.mp4"
    video.save(update_fields=["processed_streamable_relative_path"])

    probe_data: dict[str, object] = {
        "streams": [
            {
                "codec_type": "video",
                "avg_frame_rate": "50/1",
                "r_frame_rate": "50/1",
            }
        ]
    }

    monkeypatch.setattr(media_integrity, "STORAGE_DIR", tmp_path)

    probed_paths: list[Path] = []

    def fake_probe_video_path(path: Path) -> tuple[bool, dict[str, object], str]:
        probed_paths.append(path)
        return True, probe_data, ""

    monkeypatch.setattr(
        media_integrity,
        "_probe_video_path",
        fake_probe_video_path,
    )

    summary = reconcile_media_integrity(
        dry_run=True,
        video_ids=[video.pk],
        check_ffmpeg_meta=True,
    )

    report = summary.video_reports[0]["ffmpeg_metadata"]
    assert report["source"] == "unavailable"
    assert report["fps_provenance"] == "fps_defaulted"
    assert report["probed_fps"] is None
    assert report["action"] == "fps_defaulted"
    assert report["default_fps"] == 50.0
    assert probed_paths == []


def test_legacy_streamable_probe_reports_removal_only_in_dry_run(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    video = _video_with_initialized_frames(tmp_path, frame_count=3)
    streamable_path = tmp_path / "streamable" / "processed" / "fallback.mp4"
    _write_test_file(
        streamable_path,
        b"corrupt-streamable",
        file_mode=media_integrity.STREAMABLE_FILE_MODE,
    )
    video.processed_streamable_relative_path = "streamable/processed/fallback.mp4"
    video.save(update_fields=["processed_streamable_relative_path"])

    monkeypatch.setattr(media_integrity, "STORAGE_DIR", tmp_path)

    called: list[dict[str, bool]] = []

    def fake_sync(
        video_arg: VideoFile,
        *,
        include_raw: bool,
        include_processed: bool,
        save: bool,
    ) -> list[object]:
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
    assert summary.lost_records == 0
    artifact = summary.video_reports[0]["streamable_probe"]["artifacts"][0]
    assert artifact["kind"] == "processed"
    assert artifact["probe_ok"] is False
    assert artifact["action"] == "would_remove_streamable"
    assert artifact["detail"] == "legacy streamable MP4 is not allowed at rest"

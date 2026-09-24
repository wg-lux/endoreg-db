# pyright: reportPrivateUsage=false
from __future__ import annotations

from pathlib import Path
from typing import NoReturn

import pytest

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.file_storage import state_management
from endoreg_db.models import Center, VideoFile
from endoreg_db.utils import paths as paths_module


def _deny_nuke_transcoding_dir(*args: object, **kwargs: object) -> NoReturn:
    raise AssertionError("delete_associated_files must not nuke global transcoding")


def _audio_only_stream_info(path: Path) -> dict[str, list[dict[str, str]]]:
    return {"streams": [{"codec_type": "audio"}]}


@pytest.mark.django_db
@pytest.mark.parametrize("denied", [False, True])
def test_delete_associated_files_removes_streamable_artifacts_and_clears_video_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    denied: bool,
) -> None:
    center = Center.objects.create(
        name="state-storage-center",
        display_name="State Storage Center",
    )
    storage_root = paths_module.EndoregPathsModel.from_environment().storage
    raw_stream = storage_root / "streamable_videos" / "raw" / "raw-stream.mp4"
    processed_stream = (
        storage_root / "streamable_videos" / "processed" / "processed-stream.mp4"
    )
    raw_stream.parent.mkdir(parents=True, exist_ok=True)
    processed_stream.parent.mkdir(parents=True, exist_ok=True)
    raw_stream.write_bytes(b"raw-stream")
    processed_stream.write_bytes(b"processed-stream")

    video = VideoFile.objects.create(
        center=center,
        raw_video_hash="state-storage-video",
        raw_streamable_relative_path=raw_stream.relative_to(storage_root).as_posix(),
        processed_streamable_relative_path=processed_stream.relative_to(
            storage_root
        ).as_posix(),
    )
    import_file = tmp_path / "import.mp4"
    import_file.write_bytes(b"import")
    ctx = ImportContext(
        file_path=import_file,
        center_name=center.name,
        file_type="video",
    )
    ctx.current_video = video

    if denied:

        def deny(*args: object, **kwargs: object) -> NoReturn:
            raise PermissionError("denied")

        monkeypatch.setattr(state_management, "safe_unlink_file", deny)
        with pytest.raises(PermissionError):
            state_management.delete_associated_files(ctx)
        video.refresh_from_db()
        assert video.raw_streamable_relative_path and raw_stream.exists()
        return
    state_management.delete_associated_files(ctx)

    video.refresh_from_db()
    assert not raw_stream.exists()
    assert not processed_stream.exists()
    assert video.raw_streamable_relative_path == ""
    assert video.processed_streamable_relative_path == ""


@pytest.mark.django_db
def test_delete_associated_files_preserves_existing_video_artifacts_for_reimport(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import endoreg_db.import_files.file_storage.cleanup as cleanup_module

    center = Center.objects.create(
        name="state-reimport-center",
        display_name="State Re-import Center",
    )
    storage_root = paths_module.EndoregPathsModel.from_environment().storage
    raw_stream = storage_root / "streamable_videos" / "raw" / "reimport-raw.mp4"
    processed_stream = (
        storage_root / "streamable_videos" / "processed" / "reimport-processed.mp4"
    )
    raw_stream.parent.mkdir(parents=True, exist_ok=True)
    processed_stream.parent.mkdir(parents=True, exist_ok=True)
    raw_stream.write_bytes(b"raw-stream")
    processed_stream.write_bytes(b"processed-stream")

    video = VideoFile.objects.create(
        center=center,
        raw_video_hash="state-reimport-video",
        raw_streamable_relative_path=raw_stream.relative_to(storage_root).as_posix(),
        processed_streamable_relative_path=processed_stream.relative_to(
            storage_root
        ).as_posix(),
    )
    import_file = tmp_path / "import.mp4"
    staged_output = tmp_path / "staged-reimport.mp4"
    import_file.write_bytes(b"import")
    staged_output.write_bytes(b"staged")
    ctx = ImportContext(
        file_path=import_file,
        center_name=center.name,
        file_type="video",
    )
    ctx.current_video = video
    ctx.anonymized_path = staged_output

    monkeypatch.setattr(
        cleanup_module,
        "staging_cleanup_roots",
        lambda: (tmp_path,),
        raising=True,
    )

    state_management.delete_associated_files(
        ctx,
        preserve_existing_video_artifacts=True,
    )

    video.refresh_from_db()
    assert raw_stream.read_bytes() == b"raw-stream"
    assert processed_stream.read_bytes() == b"processed-stream"
    assert video.raw_streamable_relative_path != ""
    assert video.processed_streamable_relative_path != ""
    assert not staged_output.exists()
    assert ctx.anonymized_path is None


@pytest.mark.unit
def test_delete_associated_files_removes_anonymized_and_sensitive_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import endoreg_db.import_files.file_storage.cleanup as cleanup_module

    def fake_staging_cleanup_roots() -> tuple[Path, ...]:
        return (tmp_path,)

    import_file = tmp_path / "import.pdf"
    anonymized_path = tmp_path / "anon.pdf"
    sensitive_path = tmp_path / "sensitive.pdf"
    import_file.write_bytes(b"import")
    anonymized_path.write_bytes(b"anon")
    sensitive_path.write_bytes(b"sensitive")
    ctx = ImportContext(
        file_path=import_file,
        center_name="state-storage-center",
        file_type="report",
    )
    ctx.anonymized_path = anonymized_path
    ctx.sensitive_path = sensitive_path

    monkeypatch.setattr(
        state_management,
        "nuke_transcoding_dir",
        _deny_nuke_transcoding_dir,
        raising=True,
    )
    monkeypatch.setattr(
        cleanup_module,
        "staging_cleanup_roots",
        fake_staging_cleanup_roots,
        raising=True,
    )

    state_management.delete_associated_files(ctx)

    assert ctx.anonymized_path is None
    assert ctx.sensitive_path is None
    assert not anonymized_path.exists()
    assert not sensitive_path.exists()


@pytest.mark.unit
def test_delete_associated_files_preserves_active_sensitive_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import endoreg_db.import_files.file_storage.cleanup as cleanup_module

    sensitive_path = tmp_path / "sensitive.pdf"
    sensitive_path.write_bytes(b"sensitive")
    ctx = ImportContext(
        file_path=sensitive_path,
        center_name="state-storage-center",
        file_type="report",
    )
    ctx.sensitive_path = sensitive_path

    monkeypatch.setattr(
        cleanup_module,
        "staging_cleanup_roots",
        lambda: (tmp_path,),
        raising=True,
    )

    state_management.delete_associated_files(
        ctx,
        preserve_sensitive_staging=True,
    )

    assert ctx.sensitive_path == sensitive_path
    assert sensitive_path.read_bytes() == b"sensitive"


@pytest.mark.unit
def test_nuke_transcoding_dir_removes_files_symlinks_and_directories(
    tmp_path: Path,
) -> None:
    transcoding_dir = tmp_path / "transcoding"
    nested_dir = transcoding_dir / "nested"
    nested_dir.mkdir(parents=True)
    file_path = transcoding_dir / "artifact.tmp"
    target_path = transcoding_dir / "target.tmp"
    symlink_path = transcoding_dir / "artifact.link"
    file_path.write_bytes(b"artifact")
    target_path.write_bytes(b"target")
    symlink_path.symlink_to(target_path)
    (nested_dir / "child.tmp").write_bytes(b"child")

    result = state_management.nuke_transcoding_dir(transcoding_dir)

    assert result is True
    assert list(transcoding_dir.iterdir()) == []


@pytest.mark.unit
def test_nuke_transcoding_dir_returns_false_for_file_path(tmp_path: Path) -> None:
    not_a_dir = tmp_path / "not-a-dir"
    not_a_dir.write_bytes(b"payload")

    assert state_management.nuke_transcoding_dir(not_a_dir) is False
    assert not_a_dir.exists()


@pytest.mark.unit
def test_verify_final_video_output_rejects_missing_or_non_video_stream(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.mp4"
    with pytest.raises(RuntimeError, match="missing"):
        state_management._verify_final_video_output(missing)

    existing = tmp_path / "existing.mp4"
    existing.write_bytes(b"payload")
    with pytest.raises(RuntimeError, match="no video stream"):
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(
                state_management,
                "get_stream_info",
                _audio_only_stream_info,
                raising=True,
            )
            state_management._verify_final_video_output(existing)


@pytest.mark.django_db
@pytest.mark.parametrize("report", [False, True])
def test_import_failure_and_owned_retry_share_state_contract(
    tmp_path: Path, report: bool
) -> None:
    from django.core.files.base import ContentFile
    from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
    from endoreg_db.models.state.anonymization import AnonymizationState
    from endoreg_db.utils.file_operations import atomic_write_file

    center = Center.objects.create(name="import-state-contract")
    source = tmp_path / ("source.pdf" if report else "source.mp4")
    atomic_write_file(destination=source, content=[b"source"])
    instance = (
        RawPdfFile.objects.create(center=center, pdf_hash="a" * 64)
        if report
        else VideoFile.objects.create(center=center, raw_video_hash="a" * 64)
    )
    if isinstance(instance, VideoFile):
        instance.raw_file.save("source.mp4", ContentFile(b"source"))
    state = instance.get_or_create_state()
    state.anonymization_validated = True
    state.sensitive_meta_processed = True
    state.anonymized = True
    state.save()
    ctx = ImportContext(
        file_path=source,
        original_path=source,
        file_hash="a" * 64,
        center_name=center.name,
        file_type="report" if report else "video",
        retry=True,
    )
    if isinstance(instance, RawPdfFile):
        ctx.current_report = instance
    else:
        ctx.current_video = instance
    state_management.finalize_failure(ctx, preserve_existing_video_artifacts=True)
    state.refresh_from_db()
    assert state.anonymization_status == AnonymizationState.FAILED
    assert not state.processing_started
    state_management.mark_instance_processing_started(instance, ctx)
    state.refresh_from_db()
    assert state.processing_started and not state.processing_error
    assert not state.anonymization_validated and not state.sensitive_meta_processed
    assert state.anonymization_status in (
        AnonymizationState.STARTED,
        AnonymizationState.PROCESSING_ANONYMIZING,
    )


@pytest.mark.django_db
def test_failure_state_save_error_propagates_before_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from endoreg_db.models.state.video import VideoState
    from endoreg_db.models.state.processing_history.processing_history import (
        ProcessingHistory,
    )

    video = VideoFile.objects.create(
        center=Center.objects.create(name="failure-save"), raw_video_hash="b" * 64
    )
    video.get_or_create_state()
    ctx = ImportContext(
        file_path=tmp_path / "source.mp4",
        file_hash="b" * 64,
        center_name=video.center.name,
        file_type="video",
        current_video=video,
    )

    def reject_save(self: VideoState, *, save: bool = True) -> None:
        raise RuntimeError("state persistence unavailable")

    monkeypatch.setattr(VideoState, "mark_processing_failed", reject_save)
    monkeypatch.setattr(
        state_management, "delete_associated_files", _deny_nuke_transcoding_dir
    )
    with pytest.raises(RuntimeError, match="state persistence unavailable"):
        state_management.finalize_failure(ctx)
    assert not ProcessingHistory.objects.filter(file_hash="b" * 64).exists()


@pytest.mark.parametrize("unsaved", [False, True])
def test_video_finalization_rejects_invalid_instance(
    tmp_path: Path, unsaved: bool
) -> None:
    ctx = ImportContext(
        file_path=tmp_path / "source.mp4", center_name="finalization", file_type="video"
    )
    if unsaved:
        ctx.current_video = VideoFile()
    with pytest.raises(RuntimeError, match="Cannot finalize video import"):
        state_management.finalize_video_success(ctx)


@pytest.mark.parametrize("failure", ["unlink", "outside", "symlink"])
def test_failed_staging_cleanup_retains_context_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from endoreg_db.import_files.file_storage import cleanup
    from endoreg_db.utils.file_operations import atomic_write_file

    path = tmp_path / "staging.pdf"
    atomic_write_file(destination=path, content=[b"staging"])
    ctx = ImportContext(file_path=path, center_name="test", file_type="report")
    ctx.anonymized_path = ctx.sensitive_path = path
    monkeypatch.setattr(
        cleanup,
        "staging_cleanup_roots",
        lambda: () if failure == "outside" else (tmp_path,),
    )
    if failure == "unlink":

        def deny(*args: object, **kwargs: object) -> NoReturn:
            raise PermissionError("denied")

        monkeypatch.setattr(cleanup, "safe_unlink_file", deny)
    elif failure == "symlink":

        def is_symlink(candidate: Path) -> bool:
            return candidate == path

        monkeypatch.setattr(Path, "is_symlink", is_symlink)
    with pytest.raises((OSError, cleanup.StagingCleanupError)):
        state_management.delete_associated_files(ctx)
    assert ctx.anonymized_path == ctx.sensitive_path == path
    assert path.exists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("rollback", [False, True])
def test_staging_cleanup_waits_for_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rollback: bool
) -> None:
    from django.db import transaction
    from endoreg_db.import_files.file_storage import cleanup
    from endoreg_db.utils.file_operations import atomic_write_file

    path = tmp_path / "staging.pdf"
    atomic_write_file(destination=path, content=[b"staging"])
    monkeypatch.setattr(cleanup, "staging_cleanup_roots", lambda: (tmp_path,))
    with transaction.atomic():
        cleanup.cleanup_staging_after_commit((path, path, None), label="test")
        assert path.exists()
        transaction.set_rollback(rollback)
    assert path.exists() is rollback

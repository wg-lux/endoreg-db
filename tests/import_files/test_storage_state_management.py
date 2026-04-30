from __future__ import annotations

import pytest

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.file_storage import state_management
from endoreg_db.models import Center, VideoFile
from endoreg_db.utils import paths as paths_module


@pytest.mark.django_db
def test_delete_associated_files_removes_streamable_artifacts_and_clears_video_fields(
    tmp_path,
):
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
        video_hash="state-storage-video",
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

    state_management.delete_associated_files(ctx)

    video.refresh_from_db()
    assert not raw_stream.exists()
    assert not processed_stream.exists()
    assert video.raw_streamable_relative_path == ""
    assert video.processed_streamable_relative_path == ""


@pytest.mark.unit
def test_delete_associated_files_removes_anonymized_and_sensitive_paths(
    monkeypatch,
    tmp_path,
):
    import endoreg_db.import_files.file_storage.cleanup as cleanup_module

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
        lambda *args, **kwargs: True,
        raising=True,
    )
    monkeypatch.setattr(
        cleanup_module,
        "staging_cleanup_roots",
        lambda: (tmp_path,),
        raising=True,
    )

    state_management.delete_associated_files(ctx)

    assert ctx.anonymized_path is None
    assert ctx.sensitive_path is None
    assert not anonymized_path.exists()
    assert not sensitive_path.exists()


@pytest.mark.unit
def test_nuke_transcoding_dir_removes_files_symlinks_and_directories(tmp_path):
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
def test_nuke_transcoding_dir_returns_false_for_file_path(tmp_path):
    not_a_dir = tmp_path / "not-a-dir"
    not_a_dir.write_bytes(b"payload")

    assert state_management.nuke_transcoding_dir(not_a_dir) is False
    assert not_a_dir.exists()


@pytest.mark.unit
def test_verify_final_video_output_rejects_missing_or_non_video_stream(tmp_path):
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
                lambda path: {"streams": [{"codec_type": "audio"}]},
                raising=True,
            )
            state_management._verify_final_video_output(existing)

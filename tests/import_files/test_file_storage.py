from pathlib import Path

import pytest
from pytest import MonkeyPatch

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.file_storage import storage


@pytest.mark.unit
def test_create_sensitive_copy_raises_when_video_transcode_fails(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "input.mp4"
    sensitive_root = tmp_path / "sensitive"
    source.write_bytes(b"video")
    ctx = ImportContext(
        file_path=source,
        center_name="university_hospital_wuerzburg",
        file_type="video",
    )

    def fake_failed_transcode(
        source_path: Path,
        destination_path: Path,
        **kwargs: object,
    ) -> None:
        _ = source_path
        _ = destination_path
        _ = kwargs
        return None

    monkeypatch.setattr(
        storage,
        "transcode_videofile_if_required",
        fake_failed_transcode,
    )

    with pytest.raises(RuntimeError, match="Video transcode failed"):
        storage.create_sensitive_copy(source, sensitive_root, ctx)


@pytest.mark.unit
def test_create_sensitive_copy_returns_transcoded_video_path(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "input.mp4"
    sensitive_root = tmp_path / "sensitive"
    source.write_bytes(b"video")
    ctx = ImportContext(
        file_path=source,
        center_name="university_hospital_wuerzburg",
        file_type="video",
    )

    def fake_transcode(_source: Path, dest: Path) -> Path:
        dest.write_bytes(b"transcoded")
        return dest

    monkeypatch.setattr(storage, "transcode_videofile_if_required", fake_transcode)

    result = storage.create_sensitive_copy(source, sensitive_root, ctx)

    assert result.name == source.name
    assert result.parent.parent == sensitive_root
    assert result.read_bytes() == b"transcoded"


@pytest.mark.unit
def test_create_sensitive_copy_uses_unique_staging_paths_for_same_basename(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_a = tmp_path / "a" / "input.mp4"
    source_b = tmp_path / "b" / "input.mp4"
    sensitive_root = tmp_path / "sensitive"
    source_a.parent.mkdir()
    source_b.parent.mkdir()
    source_a.write_bytes(b"video-a")
    source_b.write_bytes(b"video-b")
    ctx_a = ImportContext(
        file_path=source_a,
        center_name="university_hospital_wuerzburg",
        file_type="video",
    )
    ctx_b = ImportContext(
        file_path=source_b,
        center_name="university_hospital_wuerzburg",
        file_type="video",
    )

    def fake_transcode(source: Path, dest: Path) -> Path:
        dest.write_bytes(source.read_bytes())
        return dest

    monkeypatch.setattr(storage, "transcode_videofile_if_required", fake_transcode)

    result_a = storage.create_sensitive_copy(source_a, sensitive_root, ctx_a)
    result_b = storage.create_sensitive_copy(source_b, sensitive_root, ctx_b)

    assert result_a != result_b
    assert result_a.name == "input.mp4"
    assert result_b.name == "input.mp4"
    assert result_a.parent.parent == sensitive_root
    assert result_b.parent.parent == sensitive_root
    assert result_a.read_bytes() == b"video-a"
    assert result_b.read_bytes() == b"video-b"

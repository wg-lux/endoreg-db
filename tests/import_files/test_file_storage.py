from pathlib import Path

import pytest
from pytest import MonkeyPatch

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.file_storage import storage
from endoreg_db.utils.hashs import get_file_hash


@pytest.mark.unit
def test_create_sensitive_copy_propagates_video_copy_failure(
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

    def fake_failed_copy(
        _source_path: Path,
        _destination_path: Path,
    ) -> None:
        raise OSError("copy failed")

    monkeypatch.setattr(
        storage,
        "atomic_copy_with_fallback",
        fake_failed_copy,
    )

    with pytest.raises(OSError, match="copy failed"):
        storage.create_sensitive_copy(source, sensitive_root, ctx)


@pytest.mark.unit
def test_create_sensitive_copy_returns_copied_video_path(
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
    calls: list[tuple[Path, Path]] = []

    def fake_copy(copy_source: Path, dest: Path) -> bool:
        calls.append((copy_source, dest))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(copy_source.read_bytes())
        return True

    monkeypatch.setattr(storage, "atomic_copy_with_fallback", fake_copy)

    result = storage.create_sensitive_copy(source, sensitive_root, ctx)

    assert result.name == source.name
    assert result.parent.parent == sensitive_root
    assert result.read_bytes() == b"video"
    assert calls == [(source, result)]
    assert ctx.file_hash == get_file_hash(result)


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

    result_a = storage.create_sensitive_copy(source_a, sensitive_root, ctx_a)
    result_b = storage.create_sensitive_copy(source_b, sensitive_root, ctx_b)

    assert result_a != result_b
    assert result_a.name == "input.mp4"
    assert result_b.name == "input.mp4"
    assert result_a.parent.parent == sensitive_root
    assert result_b.parent.parent == sensitive_root
    assert result_a.read_bytes() == b"video-a"
    assert result_b.read_bytes() == b"video-b"


@pytest.mark.unit
@pytest.mark.parametrize("change_source", [False, True])
def test_sensitive_copy_rejects_changed_content_before_handoff(
    monkeypatch: MonkeyPatch, tmp_path: Path, change_source: bool
) -> None:
    source = tmp_path / "input.mp4"
    source.write_bytes(b"original video")
    expected_hash = get_file_hash(source)
    ctx = ImportContext(
        file_path=source,
        center_name="university_hospital_wuerzburg",
        file_type="video",
        file_hash=expected_hash,
    )
    sensitive_root = tmp_path / "sensitive"
    if change_source:
        source.write_bytes(b"replacement video")
    else:

        def corrupt_copy(_source: Path, destination: Path) -> bool:
            destination.write_bytes(b"corrupt copy")
            return True

        monkeypatch.setattr(storage, "atomic_copy_with_fallback", corrupt_copy)

    with pytest.raises(ValueError, match="differs from import source identity"):
        storage.create_sensitive_copy(source, sensitive_root, ctx)

    assert source.read_bytes() == (
        b"replacement video" if change_source else b"original video"
    )
    assert ctx.file_hash == expected_hash
    assert ctx.current_video is None
    assert not list(sensitive_root.rglob("*.mp4"))

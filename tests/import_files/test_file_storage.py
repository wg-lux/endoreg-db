import pytest

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.file_storage import storage


@pytest.mark.unit
def test_create_sensitive_copy_raises_when_video_transcode_fails(
    monkeypatch, tmp_path
):
    source = tmp_path / "input.mp4"
    sensitive_root = tmp_path / "sensitive"
    source.write_bytes(b"video")
    ctx = ImportContext(
        file_path=source,
        center_name="university_hospital_wuerzburg",
        file_type="video",
    )

    monkeypatch.setattr(storage, "transcode_video", lambda *_args, **_kwargs: None)

    with pytest.raises(RuntimeError, match="Video transcode failed"):
        storage.create_sensitive_copy(source, sensitive_root, ctx)


@pytest.mark.unit
def test_create_sensitive_copy_returns_transcoded_video_path(monkeypatch, tmp_path):
    source = tmp_path / "input.mp4"
    sensitive_root = tmp_path / "sensitive"
    source.write_bytes(b"video")
    ctx = ImportContext(
        file_path=source,
        center_name="university_hospital_wuerzburg",
        file_type="video",
    )

    def fake_transcode(_source, dest):
        dest.write_bytes(b"transcoded")
        return dest

    monkeypatch.setattr(storage, "transcode_video", fake_transcode)

    result = storage.create_sensitive_copy(source, sensitive_root, ctx)

    assert result == sensitive_root / source.name
    assert result.read_bytes() == b"transcoded"

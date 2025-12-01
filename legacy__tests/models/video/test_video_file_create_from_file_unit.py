import hashlib
import uuid
from pathlib import Path

import pytest

import endoreg_db.models.media.video.create_from_file as create_module
import endoreg_db.models.media.video.video_file_io as io_module
from endoreg_db.models import Center, VideoFile


@pytest.mark.django_db
def test_create_from_file_stores_transcoded_copy(tmp_path, monkeypatch):
    storage_root = tmp_path / "storage"
    video_dir = storage_root / "videos"
    temp_dir = storage_root / "tmp"
    storage_root.mkdir(parents=True, exist_ok=True)
    video_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)

    source_file = tmp_path / "input.mp4"
    source_file.write_bytes(b"original-bytes")

    path_mapping = {"video": video_dir, "storage": storage_root}
    monkeypatch.setattr(create_module, "_get_data_paths", lambda: path_mapping)
    monkeypatch.setattr(create_module, "TMP_VIDEO_DIR", temp_dir)
    monkeypatch.setattr(create_module, "VIDEO_DIR", video_dir)
    monkeypatch.setitem(io_module.data_paths, "video", video_dir)
    monkeypatch.setitem(io_module.data_paths, "storage", storage_root)

    def fake_transcode(input_path: Path, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"transcoded-bytes")
        return output_path

    monkeypatch.setattr(create_module, "transcode_videofile_if_required", fake_transcode)

    fixed_uuid = uuid.uuid4()
    monkeypatch.setattr(create_module, "get_uuid_filename", lambda _path: ("stored.mp4", fixed_uuid))

    center = Center.objects.create(name="center-alpha", display_name="Center Alpha")

    video = create_module._create_from_file(
        cls_model=VideoFile,
        file_path=source_file,
        center_name=center.name,
        video_dir=video_dir,
        save=True,
        delete_source=False,
    )

    final_path = video_dir / "stored.mp4"
    assert final_path.exists(), "Transcoded file should be copied into the target video directory"
    assert source_file.exists(), "Source file must remain when delete_source is False"

    expected_hash = hashlib.sha256(b"transcoded-bytes").hexdigest()
    assert video.video_hash == expected_hash
    assert video.raw_file.name == "videos/stored.mp4"
    assert video.uuid == fixed_uuid

    temp_files = list((temp_dir / "transcoding").glob("*.mp4"))
    assert not temp_files, "Temporary transcoded files should be cleaned up"


@pytest.mark.django_db
def test_create_from_file_returns_existing_when_hash_matches(tmp_path, monkeypatch, settings):
    storage_root = tmp_path / "storage"
    video_dir = storage_root / "videos"
    temp_dir = storage_root / "tmp"
    storage_root.mkdir(parents=True, exist_ok=True)
    video_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    settings.MEDIA_ROOT = str(storage_root)

    source_file = tmp_path / "duplicate.mp4"
    source_file.write_bytes(b"new-bytes")

    path_mapping = {"video": video_dir, "storage": storage_root}
    monkeypatch.setattr(create_module, "_get_data_paths", lambda: path_mapping)
    monkeypatch.setattr(create_module, "TMP_VIDEO_DIR", temp_dir)
    monkeypatch.setattr(create_module, "VIDEO_DIR", video_dir)
    monkeypatch.setitem(io_module.data_paths, "video", video_dir)
    monkeypatch.setitem(io_module.data_paths, "storage", storage_root)

    duplicate_hash = "hash-duplicate"
    monkeypatch.setattr(create_module, "get_video_hash", lambda _path: duplicate_hash)

    def fake_transcode(input_path: Path, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"new-bytes-transcoded")
        return output_path

    monkeypatch.setattr(create_module, "transcode_videofile_if_required", fake_transcode)
    monkeypatch.setattr(create_module, "get_uuid_filename", lambda _path: ("ignored.mp4", uuid.uuid4()))

    center = Center.objects.create(name="center-beta", display_name="Center Beta")

    existing_path = video_dir / "existing.mp4"
    existing_path.write_bytes(b"existing-bytes")
    existing_video = VideoFile.objects.create(center=center, video_hash=duplicate_hash)
    existing_video.raw_file.name = "videos/existing.mp4"
    existing_video.save(update_fields=["raw_file"])

    result = create_module._create_from_file(
        cls_model=VideoFile,
        file_path=source_file,
        center_name=center.name,
        video_dir=video_dir,
        save=True,
        delete_source=False,
    )

    assert result.pk == existing_video.pk
    assert (video_dir / "existing.mp4").exists(), "Existing raw file must remain"
    assert VideoFile.objects.count() == 1


@pytest.mark.django_db
def test_create_from_file_cleans_up_on_missing_center(tmp_path, monkeypatch):
    storage_root = tmp_path / "storage"
    video_dir = storage_root / "videos"
    temp_dir = storage_root / "tmp"
    storage_root.mkdir(parents=True, exist_ok=True)
    video_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)

    source_file = tmp_path / "orphan.mp4"
    source_file.write_bytes(b"unused")

    path_mapping = {"video": video_dir, "storage": storage_root}
    monkeypatch.setattr(create_module, "_get_data_paths", lambda: path_mapping)
    monkeypatch.setattr(create_module, "TMP_VIDEO_DIR", temp_dir)
    monkeypatch.setattr(create_module, "VIDEO_DIR", video_dir)
    monkeypatch.setitem(io_module.data_paths, "video", video_dir)
    monkeypatch.setitem(io_module.data_paths, "storage", storage_root)

    def fake_transcode(input_path: Path, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"temp")
        return output_path

    monkeypatch.setattr(create_module, "transcode_videofile_if_required", fake_transcode)

    generated_name = "cleanup.mp4"
    monkeypatch.setattr(create_module, "get_uuid_filename", lambda _path: (generated_name, uuid.uuid4()))

    with pytest.raises(ValueError):
        create_module._create_from_file(
            cls_model=VideoFile,
            file_path=source_file,
            center_name="missing-center",
            video_dir=video_dir,
            save=True,
            delete_source=False,
        )

    assert not (video_dir / generated_name).exists(), "Intermediate file should be removed on failure"
    temp_transcoded_dir = temp_dir / "transcoding"
    residuals = list(temp_transcoded_dir.glob("*"))
    assert not residuals, "Temporary files should be cleaned even when errors occur"

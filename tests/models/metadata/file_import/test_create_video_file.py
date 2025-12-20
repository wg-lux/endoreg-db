import shutil
from pathlib import Path

import pytest

from endoreg_db.models import Center, EndoscopyProcessor
from endoreg_db.exceptions import InsufficientStorageError, TranscodingError
from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.file_storage import create_video_file
import endoreg_db.models.media.video.create_from_file as create_from_file_module
from endoreg_db.utils import paths as paths_module
from endoreg_db.utils.file_operations import sha256_file
import endoreg_db.utils as utils
from endoreg_db.models.state.processing_history import ProcessingHistory


def _configure_storage_layout(test_suffix: str) -> tuple[Path, Path, Path]:
    """
    Helper: create a storage layout *inside* the real STORAGE_DIR so that
    to_storage_relative() produces proper relative FileField names.
    """
    base = paths_module.STORAGE_DIR / f"pytest_create_video_file_{test_suffix}"
    storage_root = base
    sensitive_dir = storage_root / "sensitive_videos"
    transcoding_dir = storage_root / "transcoding"

    storage_root.mkdir(parents=True, exist_ok=True)
    sensitive_dir.mkdir(parents=True, exist_ok=True)
    transcoding_dir.mkdir(parents=True, exist_ok=True)

    # Wire endoreg_db.utils.data_paths so _get_data_paths() sees our paths
    # NOTE: keep "storage" pointing to the real STORAGE_DIR, only override sensitive/transcoding
    utils.data_paths = {
        **getattr(utils, "data_paths", {}),
        "storage": paths_module.STORAGE_DIR,
        "sensitive_video": sensitive_dir,
        "transcoding": transcoding_dir,
    }

    return storage_root, sensitive_dir, transcoding_dir


@pytest.fixture(autouse=True)
def _patch_video_initialize(monkeypatch):
    """
    For these tests we don't care about ffprobe/OpenCV/frames.

    Patch VideoFile.initialize to be a no-op so that create_from_file_initialized
    doesn't try to read real video specs from our dummy MP4 bytes.
    """
    import endoreg_db.models.media.video.video_file as video_file_module

    def fake_initialize(self):
        # just return self without touching metadata / frames
        return self

    monkeypatch.setattr(
        video_file_module.VideoFile,
        "initialize",
        fake_initialize,
        raising=True,
    )


@pytest.mark.django_db
def test_create_from_file_happy_path(tmp_path, monkeypatch, base_db_data):
    """
    Happy path: new VideoFile is created, file is stored under the configured
    sensitive_video directory, and get_raw_file_path() returns a regular file.
    """
    # --- Arrange: fake storage layout ---------------------------------------
    storage_root, sensitive_dir, transcoding_dir = _configure_storage_layout("happy")

    # Patch TRANSCODING_DIR in the module that actually uses it
    monkeypatch.setattr(
        create_from_file_module,
        "TRANSCODING_DIR",
        transcoding_dir,
        raising=True,
    )

    # Fake transcoder: copy input -> output and return output path
    def fake_transcode(input_path: Path, output_path: Path) -> Path:
        shutil.copy2(input_path, output_path)
        return output_path

    monkeypatch.setattr(
        create_from_file_module,
        "transcode_videofile_if_required",
        fake_transcode,
        raising=True,
    )


    # --- Arrange: dummy input video -----------------------------------------
    import_dir = tmp_path / "import"
    import_dir.mkdir(parents=True, exist_ok=True)
    src_file = import_dir / "test_small_intestine.mp4"
    src_file.write_bytes(
        b"\x00\x00\x00\x20ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 1000
    )

    # Center / Processor from base_db_data
    try:
        assert Center.objects.first() is not None
        assert EndoscopyProcessor.objects.first() is not None
        center_name = Center.objects.first().name
        processor_name = EndoscopyProcessor.objects.first().name
    except Exception as e:
        pytest.fail(
            f"Failed to retrieve center/processor names, might be none were available: {str(e)}"
        )

    ctx = ImportContext(
        file_path=src_file,
        center_name=center_name,
        processor_name=processor_name,
        delete_source=False,  # keep import file
        original_path=Path(src_file),
    )

    # --- Act ----------------------------------------------------------------
    video, created, needs_processing = create_video_file.create_or_retrieve_video_file(
        ctx
    )
    # --- Assert -------------------------------------------------------------
    assert needs_processing is True
    assert created is False
    assert video.pk is not None
    assert isinstance(video.get_raw_file_path(), Path)
    raw_path = video.get_raw_file_path()
    assert raw_path is not None
    assert raw_path.exists()
    assert raw_path.is_file()
    assert video.video_hash == sha256_file(raw_path)


@pytest.mark.django_db
def test_create_from_file_duplicate_with_existing_file(
    tmp_path, monkeypatch, base_db_data
):
    """
    When a VideoFile with the same hash already exists *and* its raw file exists,
    create_or_retrieve_video_file should return the existing instance and not
    create a new one.
    """
    storage_root, sensitive_dir, transcoding_dir = _configure_storage_layout(
        "dup_existing"
    )

    monkeypatch.setattr(
        create_from_file_module,
        "TRANSCODING_DIR",
        transcoding_dir,
        raising=True,
    )

    def fake_transcode(input_path: Path, output_path: Path) -> Path:
        shutil.copy2(input_path, output_path)
        return output_path

    monkeypatch.setattr(
        create_from_file_module,
        "transcode_videofile_if_required",
        fake_transcode,
        raising=True,
    )


    import_dir = tmp_path / "import"
    import_dir.mkdir(parents=True, exist_ok=True)
    src_file = import_dir / "test_dup.mp4"
    src_file.write_bytes(
        b"\x00\x00\x00\x20ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 1000
    )

    center_name = Center.objects.first().name
    processor_name = EndoscopyProcessor.objects.first().name

    ctx = ImportContext(
        file_path=src_file,
        center_name=center_name,
        processor_name=processor_name,
        delete_source=False,
    )

    # First call: creates the object
    v1, processed1, needs_processing1 = create_video_file.create_or_retrieve_video_file(
        ctx
    )
    assert processed1 is False
    assert needs_processing1 is True

    raw1 = v1.get_raw_file_path()
    assert raw1 is not None
    assert raw1.exists()

    # Second call: should reuse existing instance
    ctx2 = ImportContext(
        file_path=src_file,
        center_name=center_name,
        processor_name=processor_name,
        delete_source=False,
    )
    assert isinstance(ctx.file_hash, str)
    ph = ProcessingHistory().get_or_create_for_hash(
        file_hash=ctx.file_hash,
        success=True,  # Simulate successful processing
    )
    v2, processed2, needs_processing2 = create_video_file.create_or_retrieve_video_file(
        ctx2
    )

    assert ph.has_history_for_hash(file_hash=ctx.file_hash)

    assert processed2 is True
    assert needs_processing2 is False

    assert v2.pk == v1.pk


@pytest.mark.django_db
def test_create_from_file_duplicate_with_missing_file_recreates(
    tmp_path, monkeypatch, base_db_data
):
    """
    When a VideoFile with the same hash exists but its raw file is missing,
    the orphaned record should be deleted and a new VideoFile created.
    """
    storage_root, sensitive_dir, transcoding_dir = _configure_storage_layout(
        "dup_orphan"
    )

    monkeypatch.setattr(
        create_from_file_module,
        "TRANSCODING_DIR",
        transcoding_dir,
        raising=True,
    )

    def fake_transcode(input_path: Path, output_path: Path) -> Path:
        shutil.copy2(input_path, output_path)
        return output_path

    monkeypatch.setattr(
        create_from_file_module,
        "transcode_videofile_if_required",
        fake_transcode,
        raising=True,
    )


    import_dir = tmp_path / "import"
    import_dir.mkdir(parents=True, exist_ok=True)
    src_file = import_dir / "test_orphan.mp4"
    src_file.write_bytes(
        b"\x00\x00\x00\x20ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 1000
    )

    center_name = Center.objects.first().name
    processor_name = EndoscopyProcessor.objects.first().name

    ctx = ImportContext(
        file_path=src_file,
        center_name=center_name,
        processor_name=processor_name,
        delete_source=False,
        original_path=Path(src_file),
    )

    # First create
    orphan, processed1, needs_processing1 = (
        create_video_file.create_or_retrieve_video_file(ctx)
    )
    assert processed1 is False
    assert needs_processing1 is True
    orphan_pk = orphan.pk
    raw_path = orphan.get_raw_file_path()
    assert raw_path is not None
    assert raw_path.exists()

    # Simulate missing file on disk
    raw_path.unlink()
    assert not raw_path.exists()

    # Second call with same hash: orphan should be deleted, new video created
    ctx2 = ImportContext(
        file_path=src_file,
        center_name=center_name,
        processor_name=processor_name,
        delete_source=False,
    )
    new_video, processed2, needs_processing2 = (
        create_video_file.create_or_retrieve_video_file(ctx2)
    )

    assert processed2 is True
    assert needs_processing2 is True
    assert new_video.pk == orphan_pk


def test_check_storage_capacity_raises_on_insufficient_space(tmp_path, monkeypatch):
    """
    Unit-test check_storage_capacity in isolation by faking disk_usage.
    """
    src_file = tmp_path / "video.mp4"
    src_file.write_bytes(b"0" * 1024)  # 1 KiB

    class FakeUsage:
        def __init__(self, free):
            self.total = 10 * 1024
            self.used = 0
            self.free = free

    # Make free space smaller than required_space
    monkeypatch.setattr(
        shutil,
        "disk_usage",
        lambda p: FakeUsage(free=100),  # ridiculously small
        raising=True,
    )

    # Call the function where it actually lives
    with pytest.raises(InsufficientStorageError):
        create_from_file_module.check_storage_capacity(src_file, tmp_path)


@pytest.mark.django_db
def test_create_from_file_transcoding_error_is_wrapped(
    tmp_path, monkeypatch, base_db_data
):
    """
    If the transcoder raises, create_or_retrieve_video_file should propagate TranscodingError.
    """
    storage_root, sensitive_dir, transcoding_dir = _configure_storage_layout(
        "transcode_error"
    )

    monkeypatch.setattr(
        create_from_file_module,
        "TRANSCODING_DIR",
        transcoding_dir,
        raising=True,
    )

    def failing_transcode(input_path: Path, output_path: Path):
        raise RuntimeError("ffmpeg died horribly")

    monkeypatch.setattr(
        create_from_file_module,
        "transcode_videofile_if_required",
        failing_transcode,
        raising=True,
    )

    import_dir = tmp_path / "import"
    import_dir.mkdir(parents=True, exist_ok=True)
    src_file = import_dir / "test_fail.mp4"
    src_file.write_bytes(
        b"\x00\x00\x00\x20ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 1000
    )

    center_name = Center.objects.first().name
    processor_name = EndoscopyProcessor.objects.first().name

    ctx = ImportContext(
        file_path=src_file,
        center_name=center_name,
        processor_name=processor_name,
        delete_source=False,
        original_path=Path(src_file),
    )

    with pytest.raises(TranscodingError):
        create_video_file.create_or_retrieve_video_file(ctx)

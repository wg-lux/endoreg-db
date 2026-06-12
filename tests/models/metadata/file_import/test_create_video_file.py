# pyright: reportPrivateUsage=false, reportUnusedFunction=false
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import NoReturn

import pytest
from lx_dtypes.models.contracts.json_types import JsonObject

import endoreg_db.models.media.video.video_file as video_file_module
from endoreg_db.exceptions import InsufficientStorageError
from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.file_storage import create_video_file
from endoreg_db.models import Center, EndoscopyProcessor
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.state.processing_history import ProcessingHistory
from endoreg_db.services.video_files import _imports as create_from_file_module
from endoreg_db.utils.file_operations import sha256_file
from endoreg_db.utils.paths import EndoregPathsModel
from endoreg_db.utils.storage import save_local_file


def _configure_storage_layout(
    mock_paths: EndoregPathsModel,
    test_suffix: str,
) -> tuple[Path, Path, Path]:
    """
    Helper: create video test files under the canonical storage contract.

    Tests should stay inside the real protected storage tree and use the same
    storage-relative naming assumptions as runtime code. Do not mutate
    `data_paths` here.
    """
    storage_root = mock_paths.storage
    sensitive_dir = (
        mock_paths.sensitive_video / f"pytest_create_video_file_{test_suffix}"
    )
    transcoding_dir = mock_paths.transcoding / f"pytest_create_video_file_{test_suffix}"

    sensitive_dir.mkdir(parents=True, exist_ok=True)
    transcoding_dir.mkdir(parents=True, exist_ok=True)

    return storage_root, sensitive_dir, transcoding_dir


def _valid_h264_stream_info(_path: Path) -> JsonObject:
    return {"streams": [{"codec_type": "video", "codec_name": "h264"}]}


def _empty_stream_info(_path: Path) -> JsonObject:
    return {"streams": []}


def _center_and_processor_names() -> tuple[str, str]:
    center = Center.objects.first()
    processor = EndoscopyProcessor.objects.first()
    assert center is not None
    assert processor is not None
    return center.name, processor.name


@pytest.fixture(autouse=True)
def _patch_video_initialize(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    For these tests we don't care about ffprobe/OpenCV/frames.

    Patch VideoFile.initialize to be a no-op so that create_from_file_initialized
    doesn't try to read real video specs from our dummy MP4 bytes.
    """

    def fake_initialize(self: VideoFile) -> VideoFile:
        # just return self without touching metadata / frames
        return self

    monkeypatch.setattr(
        video_file_module.VideoFile,
        "initialize",
        fake_initialize,
        raising=True,
    )


@pytest.fixture(autouse=True)
def _patch_video_stream_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        create_from_file_module,
        "get_stream_info",
        _valid_h264_stream_info,
        raising=True,
    )


@pytest.mark.django_db
def test_create_from_file_happy_path(
    mock_storage: EndoregPathsModel,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    base_db_data: None,
) -> None:
    """
    Happy path: new VideoFile is created, file is stored under the configured
    sensitive_video directory, and get_raw_file_path() returns a regular file.
    """
    # --- Arrange: fake storage layout ---------------------------------------
    _storage_root, _sensitive_dir, transcoding_dir = _configure_storage_layout(
        mock_storage, "happy"
    )

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

    center_name, processor_name = _center_and_processor_names()

    ctx = ImportContext(
        file_path=src_file,
        center_name=center_name,
        processor_name=processor_name,
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
    mock_storage: EndoregPathsModel,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    base_db_data: None,
) -> None:
    """
    When a VideoFile with the same hash already exists *and* its raw file exists,
    create_or_retrieve_video_file should return the existing instance and not
    create a new one.
    """
    _storage_root, _sensitive_dir, transcoding_dir = _configure_storage_layout(
        mock_storage, "dup_existing"
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

    center_name, processor_name = _center_and_processor_names()

    ctx = ImportContext(
        file_path=src_file,
        center_name=center_name,
        processor_name=processor_name,
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

    processed_src = import_dir / "processed_test_dup.mp4"
    processed_src.write_bytes(b"processed-duplicate-video")
    processed_hash = sha256_file(processed_src)
    save_local_file(
        v1.processed_file,
        processed_src,
        name=f"{processed_hash}.mp4",
        save=False,
    )
    v1.processed_video_hash = processed_hash
    v1.save(update_fields=["processed_file", "processed_video_hash"])
    v1.get_or_create_state().mark_anonymization_validated()

    # Second call: should reuse existing instance
    ctx2 = ImportContext(
        file_path=src_file,
        center_name=center_name,
        processor_name=processor_name,
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


def test_create_or_retrieve_success_history_unusable_processed_file_needs_processing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from endoreg_db.services.hub.media_integrity import (
        MediaIntegrityResult,
        MediaIntegrityStatus,
    )

    source_path = tmp_path / "import" / "stale-success.mp4"
    sensitive_path = tmp_path / "sensitive" / "stale-success.mp4"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    sensitive_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"source-video")
    sensitive_path.write_bytes(b"sensitive-video")

    ctx = ImportContext(
        file_path=source_path,
        center_name="university_hospital_wuerzburg",
        processor_name="olympus_cv_1500",
    )
    ctx.sensitive_path = sensitive_path

    assert isinstance(ctx.file_hash, str)
    video = video_file_module.VideoFile(video_hash=ctx.file_hash)
    video.pk = 1

    integrity_result = MediaIntegrityResult(
        ok=False,
        status=MediaIntegrityStatus.ARTIFACT_MISSING,
        reason="Required video artifact(s) are not usable: processed_file.",
        content_hash=ctx.file_hash,
        media_pk=1,
        missing_artifacts=("processed_file",),
    )

    def fake_has_success_history(*, file_hash: str, success: bool) -> bool:
        return bool(file_hash) and success

    def forbid_success_history_downgrade(*, file_hash: str, success: bool) -> NoReturn:
        raise AssertionError(
            f"self-heal reimport must not downgrade successful history for {file_hash}"
            f" with success={success}"
        )

    def fake_get_video_by_content_hash(file_hash: str) -> VideoFile:
        assert file_hash == ctx.file_hash
        return video

    def fake_check_video_media_integrity(
        _video: VideoFile,
        *,
        content_hash: str,
    ) -> MediaIntegrityResult:
        assert content_hash == ctx.file_hash
        return integrity_result

    monkeypatch.setattr(
        create_video_file.ProcessingHistory,
        "has_history_for_hash",
        staticmethod(fake_has_success_history),
        raising=True,
    )
    monkeypatch.setattr(
        create_video_file.ProcessingHistory,
        "get_or_create_for_hash",
        staticmethod(forbid_success_history_downgrade),
        raising=True,
    )
    monkeypatch.setattr(
        create_video_file,
        "get_video_by_content_hash",
        fake_get_video_by_content_hash,
        raising=True,
    )
    monkeypatch.setattr(
        create_video_file,
        "check_video_media_integrity",
        fake_check_video_media_integrity,
        raising=True,
    )

    result, processed, needs_processing = (
        create_video_file.create_or_retrieve_video_file(ctx)
    )

    assert result is video
    assert processed is False
    assert needs_processing is True
    assert ctx.current_video is video


def test_create_or_retrieve_failure_history_missing_video_imports_fresh(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "import" / "failed-before-video-save.mp4"
    sensitive_path = tmp_path / "sensitive" / "failed-before-video-save.mp4"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    sensitive_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"source-video")
    sensitive_path.write_bytes(b"sensitive-video")

    ctx = ImportContext(
        file_path=source_path,
        center_name="university_hospital_wuerzburg",
        processor_name="olympus_cv_1500",
    )
    ctx.sensitive_path = sensitive_path

    assert isinstance(ctx.file_hash, str)
    captured_file_paths: list[Path] = []
    captured_video_hashes: list[str] = []
    captured_history: list[dict[str, str | bool]] = []
    created_video = video_file_module.VideoFile(video_hash=ctx.file_hash)
    created_video.pk = 2

    def fake_has_history_for_hash(*, file_hash: str, success: bool) -> bool:
        assert file_hash == ctx.file_hash
        return success is False

    def fake_get_video_by_content_hash(file_hash: str) -> NoReturn:
        assert file_hash == ctx.file_hash
        raise video_file_module.VideoFile.DoesNotExist

    def fake_create_from_file_initialized(
        file_path: str | Path,
        center_name: str,
        processor_name: str,
        video_hash: str,
        save_video_file: bool = True,
        initialize: bool = True,
    ) -> VideoFile:
        assert center_name == ctx.center_name
        assert processor_name == ctx.processor_name
        assert save_video_file is True
        assert initialize is True
        captured_file_paths.append(Path(file_path))
        captured_video_hashes.append(video_hash)
        return created_video

    def fake_get_or_create_for_hash(
        *,
        file_hash: str,
        success: bool,
    ) -> ProcessingHistory:
        captured_history.append({"file_hash": file_hash, "success": success})
        return ProcessingHistory(file_hash=file_hash, success=success)

    def fail_finalize_failure(_ctx: ImportContext) -> NoReturn:
        raise AssertionError("missing VideoFile failure history cannot finalize files")

    def fake_ensure_center(_video: VideoFile, center_name: str) -> SimpleNamespace:
        return SimpleNamespace(name=center_name)

    monkeypatch.setattr(
        create_video_file.ProcessingHistory,
        "has_history_for_hash",
        staticmethod(fake_has_history_for_hash),
        raising=True,
    )
    monkeypatch.setattr(
        create_video_file.ProcessingHistory,
        "get_or_create_for_hash",
        staticmethod(fake_get_or_create_for_hash),
        raising=True,
    )
    monkeypatch.setattr(
        create_video_file,
        "get_video_by_content_hash",
        fake_get_video_by_content_hash,
        raising=True,
    )
    monkeypatch.setattr(
        create_video_file,
        "finalize_failure",
        fail_finalize_failure,
        raising=True,
    )
    monkeypatch.setattr(
        video_file_module.VideoFile,
        "create_from_file_initialized",
        staticmethod(fake_create_from_file_initialized),
        raising=True,
    )
    monkeypatch.setattr(
        create_video_file,
        "ensure_center",
        fake_ensure_center,
        raising=True,
    )

    result, processed, needs_processing = (
        create_video_file.create_or_retrieve_video_file(ctx)
    )

    assert result is created_video
    assert ctx.current_video is created_video
    assert captured_file_paths == [sensitive_path]
    assert captured_video_hashes == [ctx.file_hash]
    assert captured_history == [{"file_hash": ctx.file_hash, "success": False}]
    assert processed is False
    assert needs_processing is True


@pytest.mark.django_db
def test_create_from_file_duplicate_with_missing_file_reuses_existing_record_without_success_history(
    mock_storage: EndoregPathsModel,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    base_db_data: None,
) -> None:
    """
    Without a successful ProcessingHistory entry, the current create_or_retrieve
    flow reuses the existing VideoFile record from context/failure finalization
    and keeps the pipeline marked as needing processing.
    """
    _storage_root, _sensitive_dir, transcoding_dir = _configure_storage_layout(
        mock_storage, "dup_orphan"
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

    center_name, processor_name = _center_and_processor_names()

    ctx = ImportContext(
        file_path=src_file,
        center_name=center_name,
        processor_name=processor_name,
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

    ctx2 = ImportContext(
        file_path=src_file,
        center_name=center_name,
        processor_name=processor_name,
    )
    new_video, processed2, needs_processing2 = (
        create_video_file.create_or_retrieve_video_file(ctx2)
    )

    assert processed2 is False
    assert needs_processing2 is True
    assert new_video.pk == orphan_pk
    assert new_video.get_raw_file_path() is None


def test_check_storage_capacity_raises_on_insufficient_space(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Unit-test check_storage_capacity in isolation by faking disk_usage.
    """
    src_file = tmp_path / "video.mp4"
    src_file.write_bytes(b"0" * 1024)  # 1 KiB

    class FakeUsage:
        total: int
        used: int
        free: int

        def __init__(self, free: int) -> None:
            self.total = 10 * 1024
            self.used = 0
            self.free = free

    def fake_disk_usage(_path: str | Path) -> FakeUsage:
        return FakeUsage(free=100)

    # Make free space smaller than required_space
    monkeypatch.setattr(
        shutil,
        "disk_usage",
        fake_disk_usage,  # ridiculously small
        raising=True,
    )

    # Call the function where it actually lives
    with pytest.raises(InsufficientStorageError):
        create_from_file_module.check_storage_capacity(src_file, tmp_path)


@pytest.mark.django_db
def test_create_from_file_uses_unique_standardization_temp_paths(
    mock_storage: EndoregPathsModel,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    base_db_data: None,
) -> None:
    _storage_root, _sensitive_dir, transcoding_dir = _configure_storage_layout(
        mock_storage, "unique_temp_paths"
    )
    monkeypatch.setattr(
        create_from_file_module,
        "TRANSCODING_DIR",
        transcoding_dir,
        raising=True,
    )

    captured_output_paths: list[Path] = []

    def failing_transcode(*, input_path: Path, output_path: Path) -> NoReturn:
        _ = input_path
        captured_output_paths.append(output_path)
        raise RuntimeError("transcode failed before output promotion")

    monkeypatch.setattr(
        create_from_file_module,
        "transcode_videofile_if_required",
        failing_transcode,
        raising=True,
    )

    src_file = tmp_path / "same-hash.mp4"
    src_file.write_bytes(b"same video bytes")
    expected_hash = sha256_file(src_file)
    center_name, processor_name = _center_and_processor_names()

    for _attempt in range(2):
        ctx = ImportContext(
            file_path=src_file,
            center_name=center_name,
            processor_name=processor_name,
        )
        with pytest.raises(RuntimeError, match="Video standardization failed"):
            create_video_file.create_or_retrieve_video_file(ctx)

    assert len(captured_output_paths) == 2
    assert captured_output_paths[0] != captured_output_paths[1]
    actual_transcoding_dir = captured_output_paths[0].parent
    assert all(path.parent == actual_transcoding_dir for path in captured_output_paths)
    assert all(
        path.name.startswith(f"{expected_hash}.")
        and path.name.endswith(f".part{src_file.suffix}")
        for path in captured_output_paths
    )
    assert not (
        actual_transcoding_dir / f"{expected_hash}.part{src_file.suffix}"
    ).exists()


def test_verify_completed_file_rejects_non_video_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid_video = tmp_path / "invalid.mp4"
    invalid_video.write_bytes(b"not a real mp4")

    monkeypatch.setattr(
        create_from_file_module,
        "get_stream_info",
        _empty_stream_info,
        raising=True,
    )

    with pytest.raises(RuntimeError, match="no readable video stream"):
        create_from_file_module._verify_completed_file(invalid_video)


def test_create_or_retrieve_prefers_sensitive_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    The managed sensitive copy is the source of truth for VideoFile creation.
    """
    original_path = tmp_path / "import" / "watcher.mp4"
    sensitive_path = tmp_path / "sensitive" / "watcher.mp4"
    original_path.parent.mkdir(parents=True, exist_ok=True)
    sensitive_path.parent.mkdir(parents=True, exist_ok=True)
    original_path.write_bytes(b"original")
    sensitive_path.write_bytes(b"sensitive")

    ctx = ImportContext(
        file_path=original_path,
        center_name="university_hospital_wuerzburg",
        processor_name="olympus_cv_1500",
    )
    ctx.sensitive_path = sensitive_path
    ctx.file_hash = "hash-from-sensitive-copy"

    captured_file_paths: list[Path] = []
    created_video = video_file_module.VideoFile(video_hash=ctx.file_hash)
    created_video.pk = 1

    def fake_has_history_for_hash(*, file_hash: str, success: bool) -> bool:
        assert file_hash == ctx.file_hash
        assert isinstance(success, bool)
        return False

    def fake_get_or_create_for_hash(
        *,
        file_hash: str,
        success: bool,
    ) -> ProcessingHistory:
        return ProcessingHistory(file_hash=file_hash, success=success)

    def fake_create_from_file_initialized(
        file_path: str | Path,
        center_name: str,
        processor_name: str,
        video_hash: str,
        save_video_file: bool = True,
        initialize: bool = True,
    ) -> VideoFile:
        assert center_name == ctx.center_name
        assert processor_name == ctx.processor_name
        assert video_hash == ctx.file_hash
        assert save_video_file is True
        assert initialize is True
        captured_file_paths.append(Path(file_path))
        return created_video

    def fake_ensure_center(_video: VideoFile, center_name: str) -> SimpleNamespace:
        return SimpleNamespace(name=center_name)

    monkeypatch.setattr(
        create_video_file.ProcessingHistory,
        "has_history_for_hash",
        staticmethod(fake_has_history_for_hash),
        raising=True,
    )
    monkeypatch.setattr(
        create_video_file.ProcessingHistory,
        "get_or_create_for_hash",
        staticmethod(fake_get_or_create_for_hash),
        raising=True,
    )

    monkeypatch.setattr(
        video_file_module.VideoFile,
        "create_from_file_initialized",
        staticmethod(fake_create_from_file_initialized),
        raising=True,
    )
    monkeypatch.setattr(
        create_video_file,
        "ensure_center",
        fake_ensure_center,
        raising=True,
    )

    video, processed, needs_processing = (
        create_video_file.create_or_retrieve_video_file(ctx)
    )

    assert captured_file_paths == [sensitive_path]
    assert video.pk == 1
    assert processed is False
    assert needs_processing is True


@pytest.mark.django_db
def test_create_from_file_transcoding_failure_fails_closed(
    mock_storage: EndoregPathsModel,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    base_db_data: None,
) -> None:
    """
    If standardization fails, the raw import must fail closed and not commit a
    canonical managed raw file.
    """
    _storage_root, sensitive_dir, transcoding_dir = _configure_storage_layout(
        mock_storage, "transcode_fallback"
    )

    monkeypatch.setattr(
        create_from_file_module,
        "TRANSCODING_DIR",
        transcoding_dir,
        raising=True,
    )

    def failing_transcode(_input_path: Path, _output_path: Path) -> NoReturn:
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

    center_name, processor_name = _center_and_processor_names()

    ctx = ImportContext(
        file_path=src_file,
        center_name=center_name,
        processor_name=processor_name,
        original_path=Path(src_file),
    )

    expected_hash = sha256_file(src_file)
    expected_final_path = sensitive_dir / f"{expected_hash}{src_file.suffix}"

    with pytest.raises(RuntimeError, match="Video standardization failed"):
        create_video_file.create_or_retrieve_video_file(ctx)

    assert not video_file_module.VideoFile.objects.filter(
        video_hash=expected_hash
    ).exists()
    assert not expected_final_path.exists()
    assert not list(transcoding_dir.glob(f"{expected_hash}.*.part{src_file.suffix}"))


@pytest.mark.django_db
def test_create_from_file_transcoding_failure_is_retry_safe(
    mock_storage: EndoregPathsModel,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    base_db_data: None,
) -> None:
    """
    A failed standardization attempt must leave no canonical residue so a later
    retry can succeed idempotently with the same content hash.
    """
    _storage_root, sensitive_dir, transcoding_dir = _configure_storage_layout(
        mock_storage, "transcode_retry_safe"
    )

    monkeypatch.setattr(
        create_from_file_module,
        "TRANSCODING_DIR",
        transcoding_dir,
        raising=True,
    )

    call_count = {"count": 0}

    def flaky_transcode(input_path: Path, output_path: Path):
        call_count["count"] += 1
        if call_count["count"] == 1:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"partial-output")
            raise RuntimeError("first transcode attempt failed")
        shutil.copy2(input_path, output_path)
        return output_path

    monkeypatch.setattr(
        create_from_file_module,
        "transcode_videofile_if_required",
        flaky_transcode,
        raising=True,
    )

    import_dir = tmp_path / "import"
    import_dir.mkdir(parents=True, exist_ok=True)
    src_file = import_dir / "test_retry_safe.mp4"
    src_file.write_bytes(
        b"\x00\x00\x00\x20ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 1000
    )

    center_name, processor_name = _center_and_processor_names()
    expected_hash = sha256_file(src_file)
    expected_final_path = sensitive_dir / f"{expected_hash}{src_file.suffix}"

    first_ctx = ImportContext(
        file_path=src_file,
        center_name=center_name,
        processor_name=processor_name,
        original_path=Path(src_file),
    )

    with pytest.raises(RuntimeError, match="Video standardization failed"):
        create_video_file.create_or_retrieve_video_file(first_ctx)

    assert not video_file_module.VideoFile.objects.filter(
        video_hash=expected_hash
    ).exists()
    assert not expected_final_path.exists()
    assert not list(transcoding_dir.glob(f"{expected_hash}.*.part{src_file.suffix}"))

    second_ctx = ImportContext(
        file_path=src_file,
        center_name=center_name,
        processor_name=processor_name,
        original_path=Path(src_file),
    )
    video, processed, needs_processing = (
        create_video_file.create_or_retrieve_video_file(second_ctx)
    )

    raw_path = video.get_raw_file_path()
    assert video.video_hash == expected_hash
    assert raw_path is not None
    assert raw_path.exists()
    assert processed is False
    assert needs_processing is True
    assert call_count["count"] == 2

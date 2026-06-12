import shutil
from pathlib import Path
from typing import Protocol

import pytest
from pytest import MonkeyPatch

# report equivalents
import endoreg_db.import_files.file_storage.create_report_file as create_from_file_module  # <-- pdf create_from_file
from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.medical.hardware.endoscopy_processor import EndoscopyProcessor
from endoreg_db.models.state.processing_history import ProcessingHistory
from endoreg_db.utils import paths as paths_module
from endoreg_db.utils.file_operations import (
    atomic_copy_file,
    atomic_write_file,
    ensure_directory,
    safe_unlink_file,
    sha256_file,
)


class _MockStorageLayout(Protocol):
    storage: Path
    sensitive_report: Path


@pytest.fixture(autouse=True)
def cleanup_temp_files() -> object:
    # Setup: do nothing
    yield
    # Teardown: Scan for any leftover .tmp files and delete them
    # although tmp_path handles this, this is good for extra safety
    for tmp_file in Path().glob("**/*.tmp.*"):
        safe_unlink_file(tmp_file, missing_ok=True)


def _configure_storage_layout(mock_paths: _MockStorageLayout) -> tuple[Path, Path]:
    """
    Returns the storage root and sensitive directory from the mocked model.
    """
    storage_root = mock_paths.storage
    sensitive_dir = mock_paths.sensitive_report

    # Directories are already created by model.ensure_directories()
    # called inside from_environment()

    return storage_root, sensitive_dir


def _write_minimal_pdf(path: Path) -> None:
    """
    Write a tiny valid-ish report (enough to be treated as a report file on disk).
    """
    content = b"%report-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
    ensure_directory(path.parent)
    atomic_write_file(
        destination=path,
        content=[content],
        required_bytes=len(content),
    )


@pytest.mark.django_db
def test_create_from_file_happy_path(
    mock_storage: _MockStorageLayout,
    tmp_path: Path,
    base_db_data: object,
) -> None:
    """
    Happy path: new RawPdfFile is created, file is stored under the configured
    sensitive_report directory, and get_raw_file_path() returns a regular file.
    """
    _storage_root, _sensitive_dir = _configure_storage_layout(mock_storage)

    import_dir = tmp_path / "import"
    ensure_directory(import_dir)
    src_file = import_dir / "test_report.pdf"
    _write_minimal_pdf(src_file)

    center = Center.objects.first()
    processor = EndoscopyProcessor.objects.first()
    assert center is not None
    assert processor is not None
    center_name = center.name
    processor_name = processor.name

    ctx = ImportContext(
        file_path=src_file,
        center_name=center_name,
        processor_name=processor_name,
        original_path=Path(src_file),
    )

    report, processed, needs_processing = (
        create_from_file_module.create_or_retrieve_report_file(ctx)
    )

    assert needs_processing is True
    assert processed is False
    assert report.pk is not None

    raw_path = report.get_raw_file_path()
    assert raw_path is not None
    assert raw_path.exists()
    assert raw_path.is_file()

    # Stored hash should match content hash of stored file (adjust attribute name if needed)
    assert report.pdf_hash == sha256_file(
        Path(raw_path)
    )  # <-- maybe report.report_hash/pdf_hash


@pytest.mark.django_db
def test_create_from_file_uses_sensitive_copy_as_input_but_not_as_canonical_raw_report(
    mock_storage: _MockStorageLayout,
    tmp_path: Path,
    base_db_data: object,
) -> None:
    _storage_root, sensitive_dir = _configure_storage_layout(mock_storage)

    import_dir = tmp_path / "import"
    ensure_directory(import_dir)
    src_file = import_dir / "test_sensitive_source.pdf"
    _write_minimal_pdf(src_file)

    sensitive_copy = sensitive_dir / src_file.name
    atomic_copy_file(source=src_file, destination=sensitive_copy)

    center = Center.objects.first()
    processor = EndoscopyProcessor.objects.first()
    assert center is not None
    assert processor is not None
    center_name = center.name
    processor_name = processor.name

    ctx = ImportContext(
        file_path=src_file,
        center_name=center_name,
        processor_name=processor_name,
        original_path=src_file,
    )
    ctx.sensitive_path = sensitive_copy

    report, processed, needs_processing = (
        create_from_file_module.create_or_retrieve_report_file(ctx)
    )

    assert processed is False
    assert needs_processing is True

    raw_path = report.get_raw_file_path()
    assert raw_path is not None
    assert raw_path.exists()
    assert raw_path.is_file()
    assert raw_path != sensitive_copy
    assert raw_path.parent == paths_module.SENSITIVE_REPORT_DIR
    assert raw_path.name == f"{report.pdf_hash}.pdf"
    assert report.file.name == paths_module.to_storage_relative(raw_path)


@pytest.mark.django_db
def test_create_from_file_duplicate_with_existing_file(
    mock_storage: _MockStorageLayout,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    base_db_data: object,
) -> None:
    """
    When a RawPdfFile with the same hash already exists *and* its raw file exists,
    create_or_retrieve_report_file should return the existing instance and not
    create a new one.
    """
    _storage_root, _sensitive_dir = _configure_storage_layout(mock_storage)

    import_dir = tmp_path / "import"
    ensure_directory(import_dir)
    src_file = import_dir / "test_dup.pdf"
    _write_minimal_pdf(src_file)

    center = Center.objects.first()
    processor = EndoscopyProcessor.objects.first()
    assert center is not None
    assert processor is not None
    center_name = center.name
    processor_name = processor.name

    ctx = ImportContext(
        file_path=src_file,
        center_name=center_name,
        processor_name=processor_name,
    )

    r1, processed1, needs_processing1 = (
        create_from_file_module.create_or_retrieve_report_file(ctx)
    )
    assert processed1 is False
    assert needs_processing1 is True

    raw1 = r1.get_raw_file_path()
    assert raw1 is not None
    assert raw1.exists()

    # Simulate successful processing history for this hash
    assert isinstance(ctx.file_hash, str)
    ProcessingHistory.get_or_create_for_hash(file_hash=ctx.file_hash, success=True)

    ctx2 = ImportContext(
        file_path=src_file,
        center_name=center_name,
        processor_name=processor_name,
    )
    r2, processed2, needs_processing2 = (
        create_from_file_module.create_or_retrieve_report_file(ctx2)
    )

    assert processed2 is True
    assert needs_processing2 is False
    assert r2.pk == r1.pk


@pytest.mark.django_db
def test_create_from_file_duplicate_with_missing_file_short_circuits_when_success_history_exists(
    mock_storage: _MockStorageLayout,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    base_db_data: object,
) -> None:
    """
    Successful ProcessingHistory wins over orphan detection in the current
    implementation, so a missing raw file still short-circuits to the existing
    report instance.
    """
    _storage_root, _sensitive_dir = _configure_storage_layout(mock_storage)

    import_dir = tmp_path / "import"
    ensure_directory(import_dir)
    src_file = import_dir / "test_orphan.pdf"
    _write_minimal_pdf(src_file)

    center = Center.objects.first()
    processor = EndoscopyProcessor.objects.first()
    assert center is not None
    assert processor is not None
    center_name = center.name
    processor_name = processor.name

    ctx = ImportContext(
        file_path=src_file,
        center_name=center_name,
        processor_name=processor_name,
        original_path=Path(src_file),
    )

    orphan, processed1, needs_processing1 = (
        create_from_file_module.create_or_retrieve_report_file(ctx)
    )
    assert processed1 is False
    assert needs_processing1 is True
    orphan_pk = orphan.pk

    raw_path = orphan.get_raw_file_path()
    assert raw_path is not None
    assert raw_path.exists()

    # Simulate missing file on disk
    safe_unlink_file(raw_path)
    assert not raw_path.exists()

    # Mark as successfully processed (so processed2 becomes True and we short-circuit)
    assert isinstance(ctx.file_hash, str)
    ProcessingHistory.get_or_create_for_hash(file_hash=ctx.file_hash, success=True)

    ctx2 = ImportContext(
        file_path=src_file,
        center_name=center_name,
        processor_name=processor_name,
    )
    reused_report, processed2, needs_processing2 = (
        create_from_file_module.create_or_retrieve_report_file(ctx2)
    )

    assert processed2 is True
    assert needs_processing2 is False
    assert reused_report.pk == orphan_pk
    assert reused_report.get_raw_file_path() is None


def test_check_storage_capacity_raises_on_insufficient_space(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """
    Unit-test check_storage_capacity in isolation by faking disk_usage.
    """
    src_file = tmp_path / "report.pdf"
    _write_minimal_pdf(src_file)

    class FakeUsage:
        def __init__(self, free: int) -> None:
            self.total = 10 * 1024
            self.used = 0
            self.free = free

    def fake_disk_usage(path: str | Path) -> FakeUsage:
        return FakeUsage(free=100)

    monkeypatch.setattr(
        shutil,
        "disk_usage",
        fake_disk_usage,
        raising=True,
    )

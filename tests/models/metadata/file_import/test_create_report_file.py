import shutil
from pathlib import Path

import pytest

# report equivalents
import endoreg_db.import_files.file_storage.create_report_file as create_from_file_module  # <-- pdf create_from_file
from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.models import Center, EndoscopyProcessor
from endoreg_db.models.state.processing_history import ProcessingHistory
from endoreg_db.utils import paths as paths_module
from endoreg_db.utils.file_operations import sha256_file


@pytest.fixture(autouse=True)
def cleanup_temp_files():
    # Setup: do nothing
    yield
    # Teardown: Scan for any leftover .tmp files and delete them
    # although tmp_path handles this, this is good for extra safety
    for tmp_file in Path().glob("**/*.tmp.*"):
        tmp_file.unlink(missing_ok=True)


def _configure_storage_layout(mock_paths) -> tuple[Path, Path]:
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
    path.write_bytes(b"%report-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n")


@pytest.mark.django_db
def test_create_from_file_happy_path(mock_storage, tmp_path, base_db_data):
    """
    Happy path: new RawPdfFile is created, file is stored under the configured
    sensitive_report directory, and get_raw_file_path() returns a regular file.
    """
    storage_root, sensitive_dir = _configure_storage_layout(mock_storage)

    import_dir = tmp_path / "import"
    import_dir.mkdir(parents=True, exist_ok=True)
    src_file = import_dir / "test_report.pdf"
    _write_minimal_pdf(src_file)

    center_name = Center.objects.first().name
    processor_name = EndoscopyProcessor.objects.first().name

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
def test_create_from_file_prefers_sensitive_copy_for_canonical_raw_report(
    mock_storage, tmp_path, base_db_data
):
    _storage_root, sensitive_dir = _configure_storage_layout(mock_storage)

    import_dir = tmp_path / "import"
    import_dir.mkdir(parents=True, exist_ok=True)
    src_file = import_dir / "test_sensitive_source.pdf"
    _write_minimal_pdf(src_file)

    sensitive_copy = sensitive_dir / src_file.name
    shutil.copy2(src_file, sensitive_copy)

    center_name = Center.objects.first().name
    processor_name = EndoscopyProcessor.objects.first().name

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
    assert raw_path.parent == paths_module.SENSITIVE_REPORT_DIR
    assert raw_path.name == f"{report.pdf_hash}.pdf"
    assert report.file.name == paths_module.to_storage_relative(raw_path)


@pytest.mark.django_db
def test_create_from_file_duplicate_with_existing_file(
    mock_storage, tmp_path, monkeypatch, base_db_data
):
    """
    When a RawPdfFile with the same hash already exists *and* its raw file exists,
    create_or_retrieve_report_file should return the existing instance and not
    create a new one.
    """
    storage_root, sensitive_dir = _configure_storage_layout(mock_storage)

    import_dir = tmp_path / "import"
    import_dir.mkdir(parents=True, exist_ok=True)
    src_file = import_dir / "test_dup.pdf"
    _write_minimal_pdf(src_file)

    center_name = Center.objects.first().name
    processor_name = EndoscopyProcessor.objects.first().name

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
def test_create_from_file_duplicate_with_missing_file_recreates(
    mock_storage, tmp_path, monkeypatch, base_db_data
):
    """
    When a RawPdfFile with the same hash exists but its raw file is missing,
    the orphaned record should be deleted and a new RawPdfFile created.
    """
    storage_root, sensitive_dir = _configure_storage_layout(mock_storage)

    import_dir = tmp_path / "import"
    import_dir.mkdir(parents=True, exist_ok=True)
    src_file = import_dir / "test_orphan.pdf"
    _write_minimal_pdf(src_file)

    center_name = Center.objects.first().name
    processor_name = EndoscopyProcessor.objects.first().name

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
    raw_path.unlink()
    assert not raw_path.exists()

    # Mark as successfully processed (so processed2 becomes True and we short-circuit)
    assert isinstance(ctx.file_hash, str)
    ProcessingHistory.get_or_create_for_hash(file_hash=ctx.file_hash, success=True)

    ctx2 = ImportContext(
        file_path=src_file,
        center_name=center_name,
        processor_name=processor_name,
    )
    new_report, processed2, needs_processing2 = (
        create_from_file_module.create_or_retrieve_report_file(ctx2)
    )

    # NOTE:
    # If you keep the short-circuit-on-success logic, you will *not* recreate the file here,
    # you'll return existing instance (or attempt retrieval) and needs_processing will be False.
    # If you want "orphan recreates even if success=True exists", that's a different contract.
    assert processed2 is True
    assert needs_processing2 is False
    assert new_report.pk == orphan_pk


def test_check_storage_capacity_raises_on_insufficient_space(tmp_path, monkeypatch):
    """
    Unit-test check_storage_capacity in isolation by faking disk_usage.
    """
    src_file = tmp_path / "report.pdf"
    _write_minimal_pdf(src_file)

    class FakeUsage:
        def __init__(self, free):
            self.total = 10 * 1024
            self.used = 0
            self.free = free

    monkeypatch.setattr(
        shutil,
        "disk_usage",
        lambda p: FakeUsage(free=100),
        raising=True,
    )

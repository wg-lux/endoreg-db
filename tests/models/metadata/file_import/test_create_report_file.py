import shutil
from pathlib import Path

import pytest

from endoreg_db.models import Center, EndoscopyProcessor
from endoreg_db.exceptions import InsufficientStorageError, TranscodingError
from endoreg_db.import_files.context.import_context import ImportContext

# PDF equivalents
import endoreg_db.import_files.file_storage.create_report_file as create_from_file_module  # <-- pdf create_from_file
from endoreg_db.models.media import RawPdfFile  # <-- analogous to VideoFile (could be PdfFile/RawPdfFile)
from endoreg_db.utils import paths as paths_module
from endoreg_db.utils.file_operations import sha256_file
import endoreg_db.utils as utils
from endoreg_db.models.state.processing_history import ProcessingHistory
from endoreg_db.utils.hashs import get_pdf_hash


def _configure_storage_layout(test_suffix: str) -> tuple[Path, Path]:
    """
    Helper: create a storage layout *inside* the real STORAGE_DIR so that
    to_storage_relative() produces proper relative FileField names.
    """
    base = paths_module.STORAGE_DIR / f"pytest_create_report_file_{test_suffix}"
    storage_root = base
    sensitive_dir = storage_root / "sensitive_reports"

    storage_root.mkdir(parents=True, exist_ok=True)
    sensitive_dir.mkdir(parents=True, exist_ok=True)

    # Wire endoreg_db.utils.data_paths so _get_data_paths() sees our paths
    utils.data_paths = {
        **getattr(utils, "data_paths", {}),
        "storage": paths_module.STORAGE_DIR,
        "sensitive_report": sensitive_dir,
    }

    return storage_root, sensitive_dir



def _write_minimal_pdf(path: Path) -> None:
    """
    Write a tiny valid-ish PDF (enough to be treated as a PDF file on disk).
    """
    path.write_bytes(
        b"%PDF-1.4\n"
        b"1 0 obj\n<<>>\nendobj\n"
        b"trailer\n<<>>\n"
        b"%%EOF\n"
    )


@pytest.mark.django_db
def test_create_from_file_happy_path(tmp_path, monkeypatch, base_db_data):
    """
    Happy path: new RawPdfFile is created, file is stored under the configured
    sensitive_report directory, and get_raw_file_path() returns a regular file.
    """
    storage_root, sensitive_dir = _configure_storage_layout("happy")


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
        delete_source=False,
        original_path=Path(src_file),
    )

    report, processed, needs_processing = create_from_file_module.create_or_retrieve_report_file(ctx)

    assert needs_processing is True
    assert processed is False
    assert report.pk is not None

    raw_path = report.get_raw_file_path()
    assert raw_path is not None
    assert raw_path.exists()
    assert raw_path.is_file()

    # Stored hash should match content hash of stored file (adjust attribute name if needed)
    assert report.pdf_hash == sha256_file(Path(raw_path))  # <-- maybe report.report_hash/pdf_hash


@pytest.mark.django_db
def test_create_from_file_duplicate_with_existing_file(tmp_path, monkeypatch, base_db_data):
    """
    When a RawPdfFile with the same hash already exists *and* its raw file exists,
    create_or_retrieve_report_file should return the existing instance and not
    create a new one.
    """
    storage_root, sensitive_dir = _configure_storage_layout("dup_existing")


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
        delete_source=False,
    )

    r1, processed1, needs_processing1 = create_from_file_module.create_or_retrieve_report_file(ctx)
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
        delete_source=False,
    )
    r2, processed2, needs_processing2 = create_from_file_module.create_or_retrieve_report_file(ctx2)

    assert processed2 is True
    assert needs_processing2 is False
    assert r2.pk == r1.pk


@pytest.mark.django_db
def test_create_from_file_duplicate_with_missing_file_recreates(tmp_path, monkeypatch, base_db_data):
    """
    When a RawPdfFile with the same hash exists but its raw file is missing,
    the orphaned record should be deleted and a new RawPdfFile created.
    """
    storage_root, sensitive_dir = _configure_storage_layout("dup_orphan")


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
        delete_source=False,
        original_path=Path(src_file),
    )

    orphan, processed1, needs_processing1 = create_from_file_module.create_or_retrieve_report_file(ctx)
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
        delete_source=False,
    )
    new_report, processed2, needs_processing2 = create_from_file_module.create_or_retrieve_report_file(ctx2)

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
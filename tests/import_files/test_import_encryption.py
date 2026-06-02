from pathlib import Path

import pytest
from django.core.files.base import ContentFile

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.file_storage.create_report_file import (
    create_or_retrieve_report_file,
)
from endoreg_db.import_files.file_storage.cleanup import (
    is_safe_staging_path,
    safe_cleanup_staging_file,
)
from endoreg_db.import_files.file_storage.create_video_file import (
    create_or_retrieve_video_file,
)
from endoreg_db.import_files.file_storage.state_management import (
    finalize_report_success,
)
from endoreg_db.models import Center, EndoscopyProcessor, VideoFile
from endoreg_db.services.video_files import _imports as video_create_module
from endoreg_db.utils.filesystem import paths as paths_module
from endoreg_db.utils.encryption.encrypted import MAGIC
from endoreg_db.utils.filesystem.file_operations import sha256_file


pytestmark = pytest.mark.django_db


def _runtime_paths() -> paths_module.EndoregPathsModel:
    return paths_module.EndoregPathsModel.from_environment()


def _default_center_name() -> str:
    center = Center.objects.first()
    assert center is not None
    return center.name


def _default_processor_name() -> str:
    processor = EndoscopyProcessor.objects.first()
    assert processor is not None
    return processor.name


def _write_minimal_pdf(path: Path, marker: bytes) -> bytes:
    payload = (
        b"%PDF-1.4\n1 0 obj\n<< /Marker ("
        + marker
        + b") >>\nendobj\ntrailer\n<<>>\n%%EOF\n"
    )
    path.write_bytes(payload)
    return payload


def test_report_import_persists_raw_pdf_as_encrypted_bytes(tmp_path, base_db_data):
    source = tmp_path / "raw-report.pdf"
    plaintext = _write_minimal_pdf(source, b"report-import-encryption")
    center_name = _default_center_name()

    ctx = ImportContext(file_path=source, center_name=center_name, file_type="report")

    report, processed, needs_processing = create_or_retrieve_report_file(ctx)

    assert processed is False
    assert needs_processing is True
    runtime_paths = _runtime_paths()
    stored_path = Path(report.file.path)
    assert report.file.name.startswith(f"{runtime_paths.sensitive_report.name}/")
    assert stored_path.is_relative_to(runtime_paths.sensitive_report)
    assert stored_path.read_bytes().startswith(MAGIC)
    with report.file.open("rb") as stored:
        assert stored.read() == plaintext
    assert sha256_file(source) == sha256_file(report.file)


def test_video_import_persists_raw_video_as_encrypted_bytes(
    tmp_path, monkeypatch, base_db_data
) -> None:
    def fake_initialize(self):
        return self

    def fake_transcode(input_path: Path, output_path: Path) -> Path:
        output_path.write_bytes(input_path.read_bytes())
        return output_path

    def fake_get_stream_info(_path: Path) -> dict:
        return {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "pix_fmt": "yuv420p",
                    "color_range": "pc",
                }
            ]
        }

    monkeypatch.setattr(VideoFile, "initialize", fake_initialize, raising=True)
    monkeypatch.setattr(
        video_create_module,
        "transcode_videofile_if_required",
        fake_transcode,
        raising=True,
    )
    monkeypatch.setattr(
        video_create_module,
        "get_stream_info",
        fake_get_stream_info,
        raising=True,
    )

    source = tmp_path / "raw-video.mp4"
    plaintext = b"\x00\x00\x00\x20ftypmp42" + b"video-import-encryption"
    source.write_bytes(plaintext)
    center_name = _default_center_name()
    processor_name = _default_processor_name()

    ctx = ImportContext(
        file_path=source,
        center_name=center_name,
        processor_name=processor_name,
        file_type="video",
    )

    video, processed, needs_processing = create_or_retrieve_video_file(ctx)

    assert processed is False
    assert needs_processing is True
    runtime_paths = _runtime_paths()
    stored_path = Path(video.raw_file.path)
    raw_file_name = video.raw_file.name
    assert raw_file_name is not None
    assert raw_file_name.startswith(f"{runtime_paths.sensitive_video.name}/")
    assert stored_path.is_relative_to(runtime_paths.sensitive_video)
    assert stored_path.read_bytes().startswith(MAGIC)
    with video.raw_file.open("rb") as stored:
        assert stored.read() == plaintext
    assert sha256_file(source) == sha256_file(video.raw_file)
    assert not list(stored_path.parent.glob("*.part.*"))

    video.processed_file.save(
        "processed-video.mp4",
        ContentFile(b"\x00\x00\x00\x20ftypmp42processed"),
        save=False,
    )
    processed_path = Path(video.processed_file.path)
    processed_file_name = video.processed_file.name
    assert processed_file_name is not None
    assert processed_file_name.startswith(f"{runtime_paths.anonym_video.name}/")
    assert processed_path.is_relative_to(runtime_paths.anonym_video)


def test_report_finalize_persists_processed_pdf_as_encrypted_bytes(
    tmp_path, base_db_data
):
    source = tmp_path / "raw-report.pdf"
    _write_minimal_pdf(source, b"report-processed-encryption-raw")
    center_name = _default_center_name()
    ctx = ImportContext(file_path=source, center_name=center_name, file_type="report")
    report, _, _ = create_or_retrieve_report_file(ctx)

    processed_plaintext = _write_minimal_pdf(
        tmp_path / "processed-report.pdf",
        b"report-processed-encryption-output",
    )
    ctx.current_report = report
    ctx.anonymized_path = tmp_path / "processed-report.pdf"
    ctx.file_hash = report.pdf_hash

    finalize_report_success(ctx)
    report.refresh_from_db()

    runtime_paths = _runtime_paths()
    stored_path = Path(report.processed_file.path)
    assert report.processed_file.name.startswith(f"{runtime_paths.anonym_report.name}/")
    assert stored_path.is_relative_to(runtime_paths.anonym_report)
    assert stored_path.read_bytes().startswith(MAGIC)
    with report.processed_file.open("rb") as stored:
        assert stored.read() == processed_plaintext


def test_staging_cleanup_rejects_paths_outside_known_roots(tmp_path):
    outside_file = tmp_path / "outside.bin"
    outside_file.write_bytes(b"do-not-delete")

    cleaned = safe_cleanup_staging_file(
        outside_file,
        label="outside cleanup regression",
        missing_ok=False,
    )

    assert cleaned is False
    assert outside_file.read_bytes() == b"do-not-delete"


def test_is_safe_staging_path_is_explicitly_root_scoped(tmp_path):
    staging_root = tmp_path / "staging"
    staging_root.mkdir()
    staged_file = staging_root / "payload.bin"
    outside_file = tmp_path / "outside.bin"

    assert is_safe_staging_path(staged_file, allowed_roots=[staging_root])
    assert not is_safe_staging_path(outside_file, allowed_roots=[staging_root])

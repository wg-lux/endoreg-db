from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.files.base import ContentFile

from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.pdf.report_file import AnonymExaminationReport
from endoreg_db.utils import file_operations
from endoreg_db.utils.paths import get_runtime_paths
from endoreg_db.utils.storage.report_fields import ReportArtifactFieldFile

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


@pytest.mark.parametrize("kind", ["raw", "processed", "generated"])
def test_pdf_fields_share_storage_contract(kind: str) -> None:
    report = RawPdfFile(pdf_hash=hashlib.sha256(PDF).hexdigest())
    field = (
        AnonymExaminationReport().file
        if kind == "generated"
        else report.processed_file
        if kind == "processed"
        else report.file
    )
    assert isinstance(field, ReportArtifactFieldFile)
    assert not field.exists()
    field.save("contract.pdf", ContentFile(PDF), save=False)
    try:
        assert field.exists()
        assert field.get_hash() == hashlib.sha256(PDF).hexdigest()
        if kind != "generated":
            assert field.local_plaintext_path() is None
        with field.ensure_local() as path:
            assert path.is_relative_to(get_runtime_paths().storage)
            assert path.read_bytes() == PDF
    finally:
        field.delete(save=False)
    assert not field.exists()


@pytest.mark.django_db
def test_unsaved_pdf_hashes_upload_before_model_save() -> None:
    report = RawPdfFile(file=ContentFile(PDF, name="source.pdf"))
    report.save()
    assert report.pdf_hash == hashlib.sha256(PDF).hexdigest()
    assert isinstance(report.file, ReportArtifactFieldFile)
    assert report.file.get_hash() == report.pdf_hash


@pytest.mark.django_db
def test_storage_failure_prevents_report_row_deletion() -> None:
    report = RawPdfFile.objects.create(file=ContentFile(PDF, name="source.pdf"))
    with patch.object(
        report.file.storage, "delete", side_effect=OSError("storage failed")
    ):
        with pytest.raises(OSError, match="storage failed"):
            report.delete()
    assert RawPdfFile.objects.filter(pk=report.pk).exists()


def test_upload_hash_failure_restores_cursor_and_removes_staging() -> None:
    content = ContentFile(PDF)
    content.seek(5)
    paths: list[Path] = []

    def reject(path: Path) -> str:
        paths.append(path)
        assert path.parent == get_runtime_paths().transcoding
        assert path.read_bytes() == PDF
        raise ValueError("hash failed")

    with patch.object(file_operations, "get_file_hash", side_effect=reject):
        with pytest.raises(ValueError, match="hash failed"):
            ReportArtifactFieldFile.hash_content(content)
    assert content.tell() == 5
    assert paths and all(not path.exists() for path in paths)


def test_snapshot_rejects_source_mutation_before_publication(tmp_path: Path) -> None:
    source, destination = tmp_path / "source.pdf", tmp_path / "snapshot.pdf"
    source.write_bytes(PDF)
    original_hash = file_operations.get_file_hash

    def mutate(path: Path) -> str:
        digest = original_hash(path)
        if path == source:
            source.write_bytes(PDF + b"changed")
        return digest

    with patch.object(file_operations, "get_file_hash", side_effect=mutate):
        with pytest.raises((RuntimeError, ValueError)):
            file_operations.atomic_report_source_snapshot(
                source=source, destination=destination
            )
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == [source]

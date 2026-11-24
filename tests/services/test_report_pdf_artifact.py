from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import cast

import pymupdf
import pytest
from PIL import Image

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.pdf.report_file import AnonymExaminationReport
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.report.patient_examination_report import PatientExaminationReport
from endoreg_db.services.raw_pdf_files.types import PdfDocument
from endoreg_db.services.reports.pdf_renderer import ReportPdfRendererError
from endoreg_db.services.reports.persistence import (
    ReportPersistenceValidationError,
    persist_report_pdf_artifact,
)
from endoreg_db.utils.file_operations import atomic_write_file
from endoreg_db.utils.paths import get_runtime_paths

pytestmark = pytest.mark.django_db


@pytest.fixture
def report_context() -> tuple[PatientExaminationReport, PatientExamination]:
    center = Center.objects.create(name="report-pdf-artifact")
    patient = Patient.objects.create(patient_hash="report-pdf-artifact", center=center)
    examination = PatientExamination.objects.create(patient=patient)
    report = PatientExaminationReport.objects.create(
        patient_examination=examination,
        template_name="vollstaendiger_bericht",
        title="Vollstaendiger Bericht",
    )
    return report, examination


def test_default_renderer_preserves_long_structured_report_and_image(
    report_context: tuple[PatientExaminationReport, PatientExamination],
    tmp_path: Path,
) -> None:
    report, examination = report_context
    long_text = "\n".join(
        f"Abschnitt {number}: "
        + " ".join(f"befund{number}wort{part}" for part in range(25))
        + f" Ende {number}."
        for number in range(80)
    )
    frame_path = tmp_path / "selected-frame.png"
    Image.new("RGB", (8, 8), (0, 255, 0)).save(frame_path)

    full_report_id, raw_pdf_id = persist_report_pdf_artifact(
        report,
        examination,
        rendered_text=long_text,
        section_blocks=[
            {
                "type": "sentence_group",
                "section_title": "Diagnosen",
                "variables": {},
                "sentences": [
                    {
                        "template": "{text}",
                        "enabled": True,
                        "variables": {"text": long_text},
                    }
                ],
            },
            {"type": "paragraph", "text": "Weiteres Vorgehen besprechen."},
        ],
        frame_image_paths=[str(frame_path)],
        frame_captions=["Klinisch ausgewaehltes Bild"],
    )

    assert full_report_id is not None and raw_pdf_id is not None
    full_report = AnonymExaminationReport.objects.get(pk=full_report_id)
    assert full_report.text == long_text
    raw_pdf = RawPdfFile.objects.get(pk=raw_pdf_id)
    with raw_pdf.file.ensure_local() as pdf_path:
        document = cast(PdfDocument, pymupdf.open(filename=str(pdf_path)))
        try:
            extracted = " ".join(
                document[index].get_text() for index in range(document.page_count)
            )
            assert not (Counter(long_text.split()) - Counter(extracted.split()))
            assert "Diagnosen" in extracted
            assert "Weiteres Vorgehen besprechen." in extracted
            assert document.page_count > 1
        finally:
            document.close()
        with pymupdf.open(filename=str(pdf_path)) as rendered:
            assert any(cast(list[object], page.get_images()) for page in rendered)


@pytest.mark.parametrize(
    "failure_type", [ReportPdfRendererError, OSError, RuntimeError]
)
def test_default_renderer_failure_creates_no_artifact(
    report_context: tuple[PatientExaminationReport, PatientExamination],
    monkeypatch: pytest.MonkeyPatch,
    failure_type: type[Exception],
) -> None:
    report, examination = report_context
    staging_paths: list[Path] = []
    before_reports = AnonymExaminationReport.objects.count()
    before_pdfs = RawPdfFile.objects.count()

    def fail_after_partial_write(payload: object, *, output_path: Path) -> Path:
        del payload
        staging_paths.append(output_path)
        atomic_write_file(destination=output_path, content=[b"partial pdf"])
        raise failure_type("renderer failed")

    monkeypatch.setattr(
        "endoreg_db.services.reports.pdf_renderer.render_pdf_with_rust_renderer",
        fail_after_partial_write,
    )

    with pytest.raises(failure_type, match="renderer failed"):
        persist_report_pdf_artifact(report, examination, rendered_text="Volltext")

    assert len(staging_paths) == 1
    assert not staging_paths[0].exists()
    assert AnonymExaminationReport.objects.count() == before_reports
    assert RawPdfFile.objects.count() == before_pdfs


def test_explicit_lossy_mode_is_rejected_before_rendering(
    report_context: tuple[PatientExaminationReport, PatientExamination],
) -> None:
    report, examination = report_context
    before_reports = AnonymExaminationReport.objects.count()
    before_pdfs = RawPdfFile.objects.count()
    transcoding = get_runtime_paths().transcoding
    before: set[Path] = (
        set(transcoding.glob("report-*.pdf")) if transcoding.exists() else set()
    )

    with pytest.raises(
        ReportPersistenceValidationError, match="Lossy report rendering"
    ):
        persist_report_pdf_artifact(report, examination, strict_renderer=False)

    assert set(transcoding.glob("report-*.pdf")) == before
    assert AnonymExaminationReport.objects.count() == before_reports
    assert RawPdfFile.objects.count() == before_pdfs

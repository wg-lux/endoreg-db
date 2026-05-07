from django.core.management.base import CommandError
import pytest

from endoreg_db.management.commands.evaluate_lx_anonymizer_performance import Command
from endoreg_db.models import EndoscopyProcessor


def test_evaluator_auto_discovery_excludes_text_reports(tmp_path):
    text_report = tmp_path / "report.txt"
    text_report.write_text("report text", encoding="utf-8")

    discovered = Command()._discover_inputs(
        paths=[str(text_report)],
        forced_media_type="auto",
        recursive=False,
        limit=0,
    )

    assert discovered == []


def test_evaluator_forced_report_rejects_text_reports(tmp_path):
    text_report = tmp_path / "report.txt"
    text_report.write_text("report text", encoding="utf-8")

    with pytest.raises(CommandError, match="Text report inputs bypass lx_anonymizer"):
        Command()._discover_inputs(
            paths=[str(text_report)],
            forced_media_type="report",
            recursive=False,
            limit=0,
        )


def test_evaluator_discovers_pdf_reports(tmp_path):
    pdf_report = tmp_path / "report.pdf"
    pdf_report.write_bytes(b"%PDF-1.4\n")

    discovered = Command()._discover_inputs(
        paths=[str(pdf_report)],
        forced_media_type="auto",
        recursive=False,
        limit=0,
    )

    assert discovered == [(pdf_report.resolve(), "report")]


@pytest.mark.django_db
def test_evaluator_excludes_video_inputs_when_processor_roi_is_missing(tmp_path):
    video_path = tmp_path / "video.mp4"
    report_path = tmp_path / "report.pdf"
    video_path.write_bytes(b"video")
    report_path.write_bytes(b"%PDF-1.4\n")

    filtered, skipped_count = Command()._exclude_video_inputs_without_roi(
        inputs=[(video_path, "video"), (report_path, "report")],
        processor_name="missing_processor",
    )

    assert filtered == [(report_path, "report")]
    assert skipped_count == 1


@pytest.mark.django_db
def test_evaluator_keeps_video_inputs_when_processor_roi_is_configured(tmp_path):
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"video")
    EndoscopyProcessor.objects.create(
        name="configured_processor",
        endoscope_image_x=0,
        endoscope_image_y=0,
        endoscope_image_width=100,
        endoscope_image_height=100,
        examination_date_x=1,
        examination_date_y=1,
        examination_date_width=10,
        examination_date_height=10,
        patient_first_name_x=1,
        patient_first_name_y=1,
        patient_first_name_width=10,
        patient_first_name_height=10,
        patient_last_name_x=1,
        patient_last_name_y=1,
        patient_last_name_width=10,
        patient_last_name_height=10,
        patient_dob_x=1,
        patient_dob_y=1,
        patient_dob_width=10,
        patient_dob_height=10,
    )

    filtered, skipped_count = Command()._exclude_video_inputs_without_roi(
        inputs=[(video_path, "video")],
        processor_name="configured_processor",
    )

    assert filtered == [(video_path, "video")]
    assert skipped_count == 0

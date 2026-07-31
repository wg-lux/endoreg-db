from __future__ import annotations

# pyright: reportPrivateUsage=false

from pathlib import Path
from django.core.management.base import CommandError
import pytest
from lx_dtypes.models.contracts import LxAnonymizerPerformanceRunPayload

from endoreg_db.management.commands.evaluate_lx_anonymizer_performance import (
    Command,
    _roi_is_configured,
)
from endoreg_db.models import EndoscopyProcessor


@pytest.mark.parametrize(
    ("roi", "expected"),
    [
        (None, False),
        ({"x": 0, "y": 0, "width": 100, "height": 100}, True),
        ({"x": -1, "y": 0, "width": 100, "height": 100}, False),
        ({"x": 0, "y": 0, "width": 0, "height": 100}, False),
        ({"x": 0, "width": 100, "height": 100}, False),
    ],
)
def test_roi_is_configured_requires_valid_rectangle(
    roi: dict[str, int | None] | None,
    expected: bool,
) -> None:
    assert _roi_is_configured(roi) is expected


def _performance_run(
    *,
    ok: bool,
    total_seconds: float,
    import_seconds: float,
    anonymizer_seconds: float | None,
    short_circuited: bool = False,
) -> LxAnonymizerPerformanceRunPayload:
    return LxAnonymizerPerformanceRunPayload(
        source_path="/protected/input.mp4",
        staged_path="/protected/staged.mp4",
        media_type="video",
        iteration=1,
        source_size_bytes=100,
        source_sha256="a" * 64,
        ok=ok,
        total_seconds=total_seconds,
        import_seconds=import_seconds,
        staging_seconds=0.1,
        anonymizer_seconds=anonymizer_seconds,
        process_cpu_seconds=0.5,
        max_rss_kib_delta=10,
        short_circuited=short_circuited,
    )


def test_performance_summary_uses_successful_runs_for_duration_statistics() -> None:
    summary = Command._summarize(
        [
            _performance_run(
                ok=True,
                total_seconds=3.0,
                import_seconds=2.0,
                anonymizer_seconds=1.5,
            ),
            _performance_run(
                ok=True,
                total_seconds=1.0,
                import_seconds=0.5,
                anonymizer_seconds=0.0,
                short_circuited=True,
            ),
            _performance_run(
                ok=False,
                total_seconds=9.0,
                import_seconds=8.0,
                anonymizer_seconds=None,
            ),
        ]
    )

    assert summary.total_runs == 3
    assert summary.ok_runs == 2
    assert summary.failed_runs == 1
    assert summary.short_circuited_runs == 1
    assert summary.total_seconds == 4.0
    assert summary.import_seconds.count == 2
    assert summary.import_seconds.mean == 1.25
    assert summary.anonymizer_seconds.count == 2
    assert summary.end_to_end_seconds.mean == 2.0


def test_performance_command_accepts_manifest_flags(tmp_path: Path) -> None:
    parser = Command().create_parser(
        "manage.py",
        "evaluate_lx_anonymizer_performance",
    )

    options = vars(
        parser.parse_args(
            [
                "--generate-manifest",
                "--manifest-output-dir",
                str(tmp_path),
            ]
        )
    )

    assert options["generate_manifest"] is True
    assert options["manifest_output_dir"] == str(tmp_path)


def test_evaluator_auto_discovery_excludes_text_reports(tmp_path: Path) -> None:
    text_report = tmp_path / "report.txt"
    text_report.write_text("report text", encoding="utf-8")

    discovered = Command()._discover_inputs(
        paths=[str(text_report)],
        forced_media_type="auto",
        recursive=False,
        limit=0,
    )

    assert discovered == []


def test_evaluator_forced_report_rejects_text_reports(tmp_path: Path) -> None:
    text_report = tmp_path / "report.txt"
    text_report.write_text("report text", encoding="utf-8")

    with pytest.raises(CommandError, match="Text report inputs bypass lx_anonymizer"):
        Command()._discover_inputs(
            paths=[str(text_report)],
            forced_media_type="report",
            recursive=False,
            limit=0,
        )


def test_evaluator_discovers_pdf_reports(tmp_path: Path) -> None:
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
def test_evaluator_excludes_video_inputs_when_processor_roi_is_missing(
    tmp_path: Path,
) -> None:
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
def test_evaluator_keeps_video_inputs_when_processor_roi_is_configured(
    tmp_path: Path,
) -> None:
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

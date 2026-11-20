from __future__ import annotations

from datetime import date, time
from pathlib import Path
from typing import Any, TypeAlias

import pytest
from django.core.exceptions import ValidationError

from endoreg_db.models.media.pdf.report_file import (
    AnonymExaminationReport,
    AnonymHistologyReport,
    DocumentMeta,
)


ReportModelType: TypeAlias = type[AnonymExaminationReport] | type[AnonymHistologyReport]

REPORT_MODELS: tuple[ReportModelType, ...] = (
    AnonymExaminationReport,
    AnonymHistologyReport,
)


@pytest.mark.django_db
@pytest.mark.parametrize("model_cls", REPORT_MODELS)
def test_report_file_meta_is_canonicalized_on_direct_save(
    model_cls: ReportModelType,
) -> None:
    report = model_cls.objects.create(
        meta={
            "source": "case_resolution",
            "raw_pdf_file_id": 17,
            "validated_at": date(2026, 7, 31),
            "artifact_path": Path("reports/anonymized.pdf"),
            "details": {"verified": True},
        }
    )

    report.refresh_from_db()
    assert report.meta == {
        "source": "case_resolution",
        "raw_pdf_file_id": 17,
        "validated_at": "2026-07-31",
        "artifact_path": "reports/anonymized.pdf",
        "details": {"verified": True},
    }


@pytest.mark.django_db
@pytest.mark.parametrize("model_cls", REPORT_MODELS)
@pytest.mark.parametrize(
    "meta",
    [
        ["not", "an", "object"],
        {1: "non-string key"},
        {"confidence": float("nan")},
        {"pseudo_patient_id": 0},
        {"details": {"unsupported": object()}},
    ],
)
def test_report_file_meta_rejects_invalid_direct_writes(
    model_cls: ReportModelType,
    meta: Any,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        model_cls.objects.create(meta=meta)

    assert "meta" in exc_info.value.message_dict


@pytest.mark.no_db
@pytest.mark.parametrize("model_cls", REPORT_MODELS)
@pytest.mark.parametrize("report_meta", [None, {"source": "case_resolution"}])
def test_unimplemented_examination_datetime_update_rejects_without_mutation(
    model_cls: ReportModelType, report_meta: DocumentMeta
) -> None:
    original_date, original_time = date(2026, 11, 2), time(8, 15)
    report = model_cls(date=original_date, time=original_time)

    with pytest.raises(NotImplementedError, match="Subclasses must implement"):
        report.set_examination_date_and_time(report_meta)

    assert report.date == original_date
    assert report.time == original_time

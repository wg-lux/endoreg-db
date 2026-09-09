from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, TypeAlias

import pytest
from django.core.exceptions import ValidationError

from endoreg_db.models import AnonymExaminationReport, AnonymHistologyReport


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

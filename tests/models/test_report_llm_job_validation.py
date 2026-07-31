from __future__ import annotations

from datetime import datetime

import pytest
from django.core.exceptions import ValidationError

from endoreg_db.models import ReportLlmInferenceJob


@pytest.mark.django_db
def test_report_llm_job_canonicalizes_config_and_result_on_direct_save() -> None:
    job = ReportLlmInferenceJob.objects.create(
        operation=ReportLlmInferenceJob.OPERATION_IMPORT,
        queue="llm_inference",
        config={
            "kind": "report_llm_import",
            "queue": "llm_inference",
            "request_payload": {"retry": False},
        },
        result={
            "pdf_id": 7,
            "pdf_hash": "anonymized-report-hash",
            "anonymized": True,
            "processed_file_sha256": "A" * 64,
        },
    )

    assert job.config == {
        "schema_version": "1.0",
        "kind": "report_llm_import",
        "queue": "llm_inference",
        "retry": True,
        "request_payload": {"retry": False},
    }
    assert job.result == {
        "schema_version": "1.0",
        "pdf_id": 7,
        "pdf_hash": "anonymized-report-hash",
        "anonymized": True,
        "processed_file_sha256": "a" * 64,
    }


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        (
            "config",
            {
                "kind": "report_llm_import",
                "queue": "llm_inference",
                "request_payload": {"requested_at": datetime.now()},
            },
        ),
        (
            "config",
            {
                "kind": "report_llm_import",
                "queue": "llm_inference",
                "unexpected": True,
            },
        ),
        ("result", {"processed_file_sha256": "not-a-digest"}),
        ("result", {"unexpected": True}),
    ],
)
def test_report_llm_job_rejects_invalid_direct_json_writes(
    field_name: str,
    value: object,
) -> None:
    kwargs: dict[str, object] = {
        "operation": ReportLlmInferenceJob.OPERATION_IMPORT,
        "queue": "llm_inference",
        "config": {
            "kind": "report_llm_import",
            "queue": "llm_inference",
        },
        field_name: value,
    }

    with pytest.raises(ValidationError) as exc_info:
        ReportLlmInferenceJob.objects.create(**kwargs)

    assert field_name in exc_info.value.message_dict

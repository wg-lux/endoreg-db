from __future__ import annotations

from endoreg_db.authz.policy import get_needed_role


def test_report_llm_and_upload_status_routes_are_patient_scoped():
    assert get_needed_role("report-reimport", "POST") == "patient:write"
    assert get_needed_role("report-llm-job-status", "GET") == "patient:read"
    assert get_needed_role("upload_status", "GET") == "patient:read"

    assert get_needed_role("report-llm-job-status", "GET") != "data:read"
    assert get_needed_role("report-reimport", "POST") != "data:write"

from unittest.mock import Mock

import pytest
from django.db import OperationalError, ProgrammingError

from endoreg_db import tasks
from endoreg_db.services.reports.import_fencing import ReportImportBusyError

pytestmark = pytest.mark.no_db


@pytest.mark.parametrize("reimport", [True, False])
@pytest.mark.parametrize("reason", ["busy", "outage", "invalid_query"])
def test_report_task_recovery_preserves_retry_contract(
    monkeypatch: pytest.MonkeyPatch,
    reimport: bool,
    reason: str,
) -> None:
    error = (
        ReportImportBusyError("owned")
        if reason == "busy"
        else OperationalError("protected details")
        if reason == "outage"
        else ProgrammingError("invalid query")
    )
    runner = (
        "_run_report_llm_reimport_job" if reimport else "_run_report_llm_import_job"
    )
    monkeypatch.setattr(
        f"endoreg_db.services.jobs.report_llm_jobs.{runner}",
        Mock(side_effect=error),
    )
    task = (
        tasks.run_report_llm_reimport_task
        if reimport
        else tasks.run_report_llm_import_task
    )
    assert task.max_retries is None
    retry = Mock(return_value=RuntimeError("retry scheduled"))
    monkeypatch.setattr(task, "retry", retry)
    # Invoke the bound task entry point without broker delivery.
    with pytest.raises(ProgrammingError if reason == "invalid_query" else RuntimeError):
        task.run("report-job")
    if reason == "invalid_query":
        retry.assert_not_called()
    else:
        assert retry.call_args.kwargs["max_retries"] is None
        assert retry.call_args.kwargs["countdown"] == 60
        if reason == "outage":
            assert str(retry.call_args.kwargs["exc"]) == "database_unavailable"

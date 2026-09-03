from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from workflow.scripts.import_common import (
    ReportImportJob,
    completed_receipt,
    configure_django,
    receipt_provenance,
    require_source,
    stage_lifecycle,
    write_receipt,
)

if TYPE_CHECKING:

    class _NamedInput(Protocol):
        source: str

    class _NamedOutput(Protocol):
        receipt: str

    class _NamedLog(Protocol):
        stage: str

    class _Params(Protocol):
        job: Mapping[str, object]
        django_settings_module: str | None
        batch_id: str
        config_sha256: str

    class _SnakemakeContext(Protocol):
        input: _NamedInput
        output: _NamedOutput
        log: _NamedLog
        params: _Params
        wildcards: Mapping[str, str]
        attempt: int


snakemake = cast("_SnakemakeContext", globals()["snakemake"])

job_id = str(snakemake.wildcards["job"])
log_path = getattr(getattr(snakemake, "log", None), "stage", None)
batch_id = getattr(snakemake.params, "batch_id", None)
config_sha256 = getattr(snakemake.params, "config_sha256", None)
attempt = int(getattr(snakemake, "attempt", 1))
with stage_lifecycle(
    path=Path(log_path) if log_path is not None else None,
    stage="report_import",
    job_id=job_id,
    batch_id=batch_id,
    attempt=attempt,
    config_sha256=config_sha256,
) as lifecycle:
    job = ReportImportJob.model_validate(dict(snakemake.params.job))
    source = require_source(job.source, snakemake.input.source)
    configure_django(snakemake.params.django_settings_module)

    from endoreg_db.import_files.report_import_service import ReportImportService
    from endoreg_db.utils.file_operations import sha256_file

    preflight_source_sha256 = sha256_file(source)
    report = ReportImportService().import_and_anonymize(
        file_path=source,
        center_name=job.center_name,
        retry=job.retry,
    )
    if report is None:
        raise RuntimeError("Report import completed without a persisted RawPdfFile.")

    published_content_sha256 = str(report.pdf_hash or "")
    if not published_content_sha256:
        raise RuntimeError("Report import completed without a content hash.")

    provenance = receipt_provenance(
        batch_id=batch_id,
        config_sha256=config_sha256,
        attempt=attempt,
        job_id=job_id,
        job_payload=dict(snakemake.params.job),
        started_at=lifecycle.started_at,
        duration_seconds=lifecycle.duration_seconds(),
    )
    write_receipt(
        completed_receipt(
            job_id=job_id,
            media_type="report",
            preflight_source_sha256=preflight_source_sha256,
            database_id=int(report.pk),
            published_content_sha256=published_content_sha256,
            retry_requested=job.retry,
            provenance=provenance,
        ),
        Path(snakemake.output.receipt),
    )

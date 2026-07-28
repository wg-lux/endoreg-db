from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from workflow.scripts.import_common import (
    ReportImportJob,
    completed_receipt,
    configure_django,
    require_source,
    write_receipt,
)

if TYPE_CHECKING:

    class _NamedInput(Protocol):
        source: str

    class _NamedOutput(Protocol):
        receipt: str

    class _Params(Protocol):
        job: Mapping[str, object]
        django_settings_module: str | None

    class _SnakemakeContext(Protocol):
        input: _NamedInput
        output: _NamedOutput
        params: _Params
        wildcards: Mapping[str, str]


snakemake = cast("_SnakemakeContext", globals()["snakemake"])


job = ReportImportJob.model_validate(dict(snakemake.params.job))
source = require_source(job.source, snakemake.input.source)
job_id = str(snakemake.wildcards["job"])

configure_django(snakemake.params.django_settings_module)

from endoreg_db.import_files.report_import_service import ReportImportService
from endoreg_db.utils.file_operations import sha256_file

source_sha256 = sha256_file(source)
report = ReportImportService().import_and_anonymize(
    file_path=source,
    center_name=job.center_name,
    retry=job.retry,
)
if report is None or report.pk is None:
    raise RuntimeError("Report import completed without a persisted RawPdfFile.")

content_hash = str(report.pdf_hash or "")
if not content_hash:
    raise RuntimeError("Report import completed without a content hash.")

write_receipt(
    completed_receipt(
        job_id=job_id,
        media_type="report",
        source_sha256=source_sha256,
        database_id=int(report.pk),
        content_hash=content_hash,
        retry_requested=job.retry,
    ),
    Path(snakemake.output.receipt),
)

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from workflow.scripts.import_common import (
    VideoImportJob,
    RuleResources,
    completed_receipt,
    configure_django,
    configure_stage_threads,
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
        resources: Mapping[str, object]
        django_settings_module: str | None
        batch_id: str
        config_sha256: str

    class _SnakemakeContext(Protocol):
        input: _NamedInput
        output: _NamedOutput
        log: _NamedLog
        params: _Params
        wildcards: Mapping[str, str]
        threads: int
        attempt: int


snakemake = cast("_SnakemakeContext", globals()["snakemake"])

job_id = str(snakemake.wildcards["job"])
log_path = getattr(getattr(snakemake, "log", None), "stage", None)
batch_id = getattr(snakemake.params, "batch_id", None)
config_sha256 = getattr(snakemake.params, "config_sha256", None)
attempt = int(getattr(snakemake, "attempt", 1))
with stage_lifecycle(
    path=Path(log_path) if log_path is not None else None,
    stage="video_import",
    job_id=job_id,
    batch_id=batch_id,
    attempt=attempt,
    config_sha256=config_sha256,
) as lifecycle:
    job = VideoImportJob.model_validate(dict(snakemake.params.job))
    resources = RuleResources.model_validate(dict(snakemake.params.resources))
    source = require_source(job.source, snakemake.input.source)
    rust_workers = configure_stage_threads(
        allocated_threads=int(snakemake.threads),
        resources=resources,
    )
    configure_django(snakemake.params.django_settings_module)

    from endoreg_db.import_files.video_import_service import VideoImportService
    from endoreg_db.utils.file_operations import sha256_file
    from endoreg_db.utils.rust_backend import stable_file_identities

    native_identities = stable_file_identities(
        [source],
        worker_count=rust_workers,
    )
    if native_identities is not None and len(native_identities) != 1:
        raise RuntimeError("Native batch identity returned an unexpected row count.")
    preflight_source_sha256 = (
        native_identities[0][2]
        if native_identities is not None
        else sha256_file(source)
    )
    video = VideoImportService().import_and_anonymize(
        file_path=source,
        center_name=job.center_name,
        processor_name=job.processor_name,
        retry=job.retry,
    )
    if video is None:
        raise RuntimeError("Video import completed without a persisted VideoFile.")

    published_content_sha256 = str(video.video_hash or "")
    if not published_content_sha256:
        raise RuntimeError("Video import completed without a content hash.")

    provenance = receipt_provenance(
        batch_id=getattr(snakemake.params, "batch_id", None),
        config_sha256=getattr(snakemake.params, "config_sha256", None),
        attempt=attempt,
        job_id=job_id,
        job_payload=dict(snakemake.params.job),
        started_at=lifecycle.started_at,
        duration_seconds=lifecycle.duration_seconds(),
    )
    write_receipt(
        completed_receipt(
            job_id=job_id,
            media_type="video",
            preflight_source_sha256=preflight_source_sha256,
            database_id=int(video.pk),
            published_content_sha256=published_content_sha256,
            retry_requested=job.retry,
            provenance=provenance,
        ),
        Path(snakemake.output.receipt),
    )

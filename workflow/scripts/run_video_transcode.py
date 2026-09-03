from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, cast

from workflow.scripts.import_common import (
    RuleResources,
    VideoTranscodeJob,
    VideoTranscodeReceipt,
    assert_video_reference_is_current,
    configure_django,
    configure_stage_threads,
    receipt_provenance,
    resolve_video_reference,
    stage_lifecycle,
    write_receipt,
)

if TYPE_CHECKING:

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
        input: Sequence[str]
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
    stage="video_transcode",
    job_id=job_id,
    batch_id=batch_id,
    attempt=attempt,
    config_sha256=config_sha256,
) as lifecycle:
    job = VideoTranscodeJob.model_validate(dict(snakemake.params.job))
    resources = RuleResources.model_validate(dict(snakemake.params.resources))
    configure_stage_threads(
        allocated_threads=int(snakemake.threads),
        resources=resources,
    )
    configure_django(snakemake.params.django_settings_module)

    from endoreg_db.models.media.video.video_file import VideoFile
    from endoreg_db.services.video_processed_transcode import (
        transcode_processed_video_for_storage_pressure,
    )

    reference = resolve_video_reference(job, list(snakemake.input))
    video_id = reference.video_id
    video = VideoFile.objects.get(pk=video_id)
    assert_video_reference_is_current(video, reference)
    result = transcode_processed_video_for_storage_pressure(
        video,
        apply=job.apply,
        quality_mode=job.quality_mode,
        force_cpu=job.force_cpu,
        allow_larger=job.allow_larger,
    )
    if result.status in {"failed", "skipped_missing_processed_file"}:
        raise RuntimeError(
            f"Processed video transcode failed for video={video_id}: {result.detail}"
        )
    receipt_status = cast(
        Literal[
            "changed",
            "dry_run",
            "skipped_not_smaller",
            "skipped_same_hash",
        ],
        result.status,
    )

    video.refresh_from_db(fields=["processed_video_hash"])
    published_processed_sha256 = str(video.processed_video_hash or "")
    if not published_processed_sha256:
        raise RuntimeError(
            "Processed video transcode completed without a published hash: "
            f"video={video_id}"
        )
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
        VideoTranscodeReceipt(
            job_id=job_id,
            video_id=video_id,
            status=receipt_status,
            previous_processed_sha256=result.old_hash,
            candidate_processed_sha256=result.new_hash or None,
            published_processed_sha256=published_processed_sha256,
            old_size=result.old_size,
            new_size=result.new_size,
            detail=result.detail,
            **provenance.model_dump(),
        ),
        Path(snakemake.output.receipt),
    )

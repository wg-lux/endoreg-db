from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, cast

from workflow.scripts.import_common import (
    RuleResources,
    VideoHlsMaterializationJob,
    VideoHlsReceipt,
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
    stage="video_hls_materialization",
    job_id=job_id,
    batch_id=batch_id,
    attempt=attempt,
    config_sha256=config_sha256,
) as lifecycle:
    job = VideoHlsMaterializationJob.model_validate(dict(snakemake.params.job))
    resources = RuleResources.model_validate(dict(snakemake.params.resources))
    configure_stage_threads(
        allocated_threads=int(snakemake.threads),
        resources=resources,
    )
    configure_django(snakemake.params.django_settings_module)

    from endoreg_db.models.media.video.video_file import VideoFile
    from endoreg_db.services.hls_media import materialize_video_hls

    reference = resolve_video_reference(job, list(snakemake.input))
    video_id = reference.video_id
    video = VideoFile.objects.get(pk=video_id)
    assert_video_reference_is_current(video, reference)
    result = materialize_video_hls(
        video_id,
        artifact_kind=job.artifact_kind,
        force=job.force,
    )
    if result.status == "already_materializing":
        raise RuntimeError(
            f"HLS materialization is already active for video={video_id}; "
            "the offline batch retry must reconcile readiness."
        )
    if result.status not in {"materialized", "already_ready"}:
        raise RuntimeError(
            "HLS materialization returned an unsupported terminal status: "
            f"{result.status}"
        )
    receipt_status = cast(
        Literal["materialized", "already_ready"],
        result.status,
    )
    video.refresh_from_db(fields=["video_hash", "processed_video_hash"])
    source_generation_sha256 = str(
        video.video_hash
        if result.artifact_kind == "raw"
        else video.processed_video_hash or ""
    )
    if not source_generation_sha256:
        raise RuntimeError(
            "HLS materialization completed without a source generation hash: "
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
        VideoHlsReceipt(
            job_id=job_id,
            video_id=video_id,
            artifact_kind=result.artifact_kind,
            source_generation_sha256=source_generation_sha256,
            status=receipt_status,
            key_id=result.key_id,
            playlist_relative_path=result.playlist_relative_path,
            segment_directory_relative_path=result.segment_directory_relative_path,
            segment_count=result.segment_count,
            detail=result.detail,
            **provenance.model_dump(),
        ),
        Path(snakemake.output.receipt),
    )

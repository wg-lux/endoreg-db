from __future__ import annotations

import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from endoreg_db.utils.file_operations import (
    atomic_write_file,
    ensure_directory,
    set_path_mode,
)

_JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class RuleResources(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    threads: int = Field(ge=1)
    mem_mb: int = Field(ge=1)
    rust_workers: int = Field(default=1, ge=1)
    ffmpeg_threads: int = Field(default=1, ge=1)
    gpu: int = Field(default=0, ge=0)
    runtime_minutes: int = Field(default=360, ge=1)

    @model_validator(mode="after")
    def validate_thread_budgets(self) -> "RuleResources":
        if self.rust_workers > self.threads:
            raise ValueError("rust_workers cannot exceed threads")
        if self.ffmpeg_threads > self.threads:
            raise ValueError("ffmpeg_threads cannot exceed threads")
        return self


class ImportResources(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    video: RuleResources
    report: RuleResources
    video_transcode: RuleResources | None = None
    video_hls: RuleResources | None = None

    @property
    def resolved_video_transcode(self) -> RuleResources:
        return self.video_transcode or self.video

    @property
    def resolved_video_hls(self) -> RuleResources:
        return self.video_hls or self.video


class ImportJob(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    source: Path
    center_name: str = Field(min_length=1)
    retry: bool = False


class VideoImportJob(ImportJob):
    processor_name: str = Field(min_length=1)


class ReportImportJob(ImportJob):
    @field_validator("source")
    @classmethod
    def validate_report_source_suffix(cls, source: Path) -> Path:
        if source.suffix.lower() not in {".pdf", ".txt"}:
            raise ValueError("report source must have a .pdf or .txt suffix")
        return source


class _VideoReference(Protocol):
    @property
    def video_id(self) -> int | None: ...

    @property
    def import_job(self) -> str | None: ...

    @property
    def transcode_job(self) -> str | None: ...


class VideoTranscodeJob(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    video_id: int | None = Field(default=None, ge=1)
    import_job: str | None = None
    transcode_job: None = None
    apply: bool = False
    quality_mode: Literal["fast", "balanced", "quality"] = "balanced"
    force_cpu: bool = False
    allow_larger: bool = False

    @model_validator(mode="after")
    def validate_reference(self) -> "VideoTranscodeJob":
        _validate_exactly_one_video_reference(self)
        return self


class VideoHlsMaterializationJob(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    video_id: int | None = Field(default=None, ge=1)
    import_job: str | None = None
    transcode_job: str | None = None
    artifact_kind: Literal["raw", "processed"] = "processed"
    force: bool = False

    @model_validator(mode="after")
    def validate_reference(self) -> "VideoHlsMaterializationJob":
        _validate_exactly_one_video_reference(self)
        return self


def _validate_exactly_one_video_reference(job: _VideoReference) -> None:
    references = (job.video_id, job.import_job, job.transcode_job)
    if sum(reference is not None for reference in references) != 1:
        raise ValueError(
            "exactly one of video_id, import_job, or transcode_job is required"
        )


class WorkflowConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    django_settings_module: str | None
    receipt_directory: Path
    log_directory: Path = Path("results/import_logs")
    batch_id: str | None = None
    resources: ImportResources
    video_imports: dict[str, VideoImportJob]
    report_imports: dict[str, ReportImportJob]
    video_transcodes: dict[str, VideoTranscodeJob] = Field(default_factory=dict)
    video_hls_materializations: dict[str, VideoHlsMaterializationJob] = Field(
        default_factory=dict
    )

    @field_validator("django_settings_module")
    @classmethod
    def validate_settings_module(cls, value: str | None) -> str | None:
        if value == "":
            raise ValueError("django_settings_module cannot be empty")
        return value

    @field_validator("batch_id")
    @classmethod
    def validate_batch_id(cls, value: str | None) -> str | None:
        if value is not None and _JOB_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("batch_id must be a filesystem-safe identifier")
        return value

    @model_validator(mode="after")
    def validate_job_identifiers(self) -> "WorkflowConfig":
        invalid_identifiers = sorted(
            identifier
            for identifier in (
                *self.video_imports,
                *self.report_imports,
                *self.video_transcodes,
                *self.video_hls_materializations,
            )
            if _JOB_ID_PATTERN.fullmatch(identifier) is None
        )
        if invalid_identifiers:
            invalid = ", ".join(invalid_identifiers)
            raise ValueError(f"invalid import job identifiers: {invalid}")
        missing_import_references = sorted(
            {
                job.import_job
                for job in (
                    *self.video_transcodes.values(),
                    *self.video_hls_materializations.values(),
                )
                if job.import_job is not None
                and job.import_job not in self.video_imports
            }
        )
        if missing_import_references:
            missing = ", ".join(missing_import_references)
            raise ValueError(f"unknown video import job references: {missing}")
        missing_transcode_references = sorted(
            {
                job.transcode_job
                for job in self.video_hls_materializations.values()
                if job.transcode_job is not None
                and job.transcode_job not in self.video_transcodes
            }
        )
        if missing_transcode_references:
            missing = ", ".join(missing_transcode_references)
            raise ValueError(f"unknown video transcode job references: {missing}")
        non_applying_transcode_references = sorted(
            {
                job.transcode_job
                for job in self.video_hls_materializations.values()
                if job.transcode_job is not None
                and not self.video_transcodes[job.transcode_job].apply
            }
        )
        if non_applying_transcode_references:
            invalid = ", ".join(non_applying_transcode_references)
            raise ValueError(
                f"HLS jobs cannot depend on non-applying video transcodes: {invalid}"
            )
        return self

    def configuration_sha256(self) -> str:
        payload = self.model_dump(mode="json", exclude={"batch_id"})
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class ReceiptProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    batch_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    attempt: int = Field(ge=1)
    config_sha256: str = Field(pattern=_SHA256_PATTERN)
    started_at: datetime
    completed_at: datetime
    duration_seconds: float = Field(ge=0)

    @field_validator("started_at", "completed_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("receipt timestamps must include a UTC offset")
        if value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("receipt timestamps must use UTC")
        return value

    @model_validator(mode="after")
    def validate_timestamp_order(self) -> "ReceiptProvenance":
        if self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")
        return self


class ImportReceipt(ReceiptProvenance):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.1"] = "1.1"
    job_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    media_type: Literal["video", "report"]
    preflight_source_sha256: str = Field(pattern=_SHA256_PATTERN)
    database_id: int = Field(ge=1)
    published_content_sha256: str = Field(pattern=_SHA256_PATTERN)
    retry_requested: bool


class VideoTranscodeReceipt(ReceiptProvenance):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.1"] = "1.1"
    job_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    stage: Literal["video_transcode"] = "video_transcode"
    video_id: int = Field(ge=1)
    status: Literal[
        "changed",
        "dry_run",
        "skipped_not_smaller",
        "skipped_same_hash",
    ]
    previous_processed_sha256: str = Field(pattern=_SHA256_PATTERN)
    candidate_processed_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    published_processed_sha256: str = Field(pattern=_SHA256_PATTERN)
    old_size: int = Field(ge=0)
    new_size: int = Field(ge=0)
    detail: str

    @model_validator(mode="after")
    def validate_generation_transition(self) -> "VideoTranscodeReceipt":
        if self.status in {"changed", "dry_run"}:
            if self.candidate_processed_sha256 is None:
                raise ValueError(f"{self.status} requires a candidate generation")
        if self.status == "changed":
            if self.candidate_processed_sha256 != self.published_processed_sha256:
                raise ValueError(
                    "changed transcode candidate must be the published generation"
                )
        elif self.published_processed_sha256 != self.previous_processed_sha256:
            raise ValueError(
                "a non-publishing transcode must retain the previous generation"
            )
        return self


class VideoHlsReceipt(ReceiptProvenance):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.1"] = "1.1"
    job_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    stage: Literal["video_hls_materialization"] = "video_hls_materialization"
    video_id: int = Field(ge=1)
    artifact_kind: Literal["raw", "processed"]
    source_generation_sha256: str = Field(pattern=_SHA256_PATTERN)
    status: Literal["materialized", "already_ready"]
    key_id: str
    playlist_relative_path: str
    segment_directory_relative_path: str
    segment_count: int = Field(ge=0)
    detail: str


class ResolvedVideoReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    video_id: int = Field(ge=1)
    source_video_hash: str | None = None
    processed_video_hash: str | None = None


class _PersistedVideoReference(Protocol):
    @property
    def video_hash(self) -> object: ...

    @property
    def processed_video_hash(self) -> object: ...


def configure_django(configured_settings_module: str | None) -> None:
    environment_settings_module = os.environ.get("DJANGO_SETTINGS_MODULE")
    if (
        configured_settings_module is not None
        and environment_settings_module is not None
        and environment_settings_module != configured_settings_module
    ):
        raise RuntimeError(
            "Configured django_settings_module conflicts with DJANGO_SETTINGS_MODULE."
        )

    settings_module = configured_settings_module or environment_settings_module
    if settings_module is None:
        raise RuntimeError(
            "Set django_settings_module in config/imports.yaml or export "
            "DJANGO_SETTINGS_MODULE."
        )

    os.environ["DJANGO_SETTINGS_MODULE"] = settings_module
    import django

    django.setup()


@dataclass(frozen=True)
class StageLifecycle:
    started_at: datetime
    started_monotonic: float

    def duration_seconds(self) -> float:
        return time.monotonic() - self.started_monotonic


def _write_stage_events(path: Path, events: list[dict[str, object]]) -> None:
    payload = f"{json.dumps(events, sort_keys=True, separators=(',', ':'))}\n".encode(
        "utf-8"
    )
    atomic_write_file(
        destination=path,
        content=(payload,),
        required_bytes=len(payload),
        file_mode=0o600,
        dir_mode=0o700,
    )


@contextmanager
def stage_lifecycle(
    *,
    path: Path | None,
    stage: Literal[
        "video_import",
        "report_import",
        "video_transcode",
        "video_hls_materialization",
    ],
    job_id: str,
    batch_id: str | None,
    attempt: int,
    config_sha256: str | None,
) -> Generator[StageLifecycle]:
    """
    Persist a private atomic lifecycle journal for one Snakemake stage.

    The journal intentionally excludes source paths, service exception text,
    and patient-derived values. A terminal journal retains the start event and
    exactly one success or failure event.
    """
    lifecycle = StageLifecycle(
        started_at=datetime.now(timezone.utc),
        started_monotonic=time.monotonic(),
    )
    resolved_batch_id = batch_id or f"direct-{job_id}"
    start_event: dict[str, object] = {
        "schema_version": "1.0",
        "event": "stage_started",
        "stage": stage,
        "job_id": job_id,
        "batch_id": resolved_batch_id,
        "attempt": attempt,
        "started_at": lifecycle.started_at.isoformat(),
    }
    if config_sha256 is not None:
        start_event["config_sha256"] = config_sha256
    events = [start_event]
    if path is not None:
        log_path = Path(path)
        ensure_directory(log_path.parent, dir_mode=0o700)
        _write_stage_events(log_path, events)
        set_path_mode(log_path, 0o600)
    try:
        yield lifecycle
    except BaseException as exc:
        events.append(
            {
                "schema_version": "1.0",
                "event": "stage_failed",
                "stage": stage,
                "job_id": job_id,
                "batch_id": resolved_batch_id,
                "attempt": attempt,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "duration_seconds": lifecycle.duration_seconds(),
                "error_type": type(exc).__name__,
            }
        )
        if path is not None:
            _write_stage_events(Path(path), events)
        raise
    else:
        events.append(
            {
                "schema_version": "1.0",
                "event": "stage_succeeded",
                "stage": stage,
                "job_id": job_id,
                "batch_id": resolved_batch_id,
                "attempt": attempt,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "duration_seconds": lifecycle.duration_seconds(),
            }
        )
        if path is not None:
            _write_stage_events(Path(path), events)


def configure_stage_threads(
    *,
    allocated_threads: int,
    resources: RuleResources,
) -> int:
    """
    Align native and external-library pools with Snakemake's CPU allocation.

    Snakemake can reduce ``threads`` to the available cores, so every inner
    pool is capped by the effective value visible to the running rule.
    """
    if allocated_threads < 1:
        raise ValueError("Snakemake allocated_threads must be greater than zero")
    rust_workers = min(allocated_threads, resources.rust_workers)
    ffmpeg_threads = min(allocated_threads, resources.ffmpeg_threads)
    os.environ["RAYON_NUM_THREADS"] = str(rust_workers)
    os.environ["LX_ANNOTATE_HLS_FFMPEG_THREADS"] = str(ffmpeg_threads)
    os.environ["OMP_NUM_THREADS"] = str(allocated_threads)
    os.environ["MKL_NUM_THREADS"] = str(allocated_threads)
    return rust_workers


def require_source(expected_source: Path, snakemake_source: str) -> Path:
    source = Path(snakemake_source)
    if source.resolve() != expected_source.resolve():
        raise RuntimeError("Snakemake input does not match the validated job source.")
    if source.is_symlink():
        raise RuntimeError(f"Import source must not be a symbolic link: {source}")
    if not source.is_file():
        raise FileNotFoundError(f"Import source is not a regular file: {source}")
    return source


def read_upstream_video_reference(
    paths: list[str],
    *,
    import_job: str | None,
    transcode_job: str | None,
) -> ResolvedVideoReference:
    if len(paths) != 1:
        raise RuntimeError("Exactly one upstream receipt is required.")
    payload = Path(paths[0]).read_text(encoding="utf-8")
    if import_job is not None:
        try:
            receipt = ImportReceipt.model_validate_json(payload)
        except ValueError as exc:
            raise RuntimeError(
                f"Invalid video import receipt schema: {paths[0]}"
            ) from exc
        if receipt.media_type != "video" or receipt.job_id != import_job:
            raise RuntimeError(
                "Video import receipt stage or job identity does not match "
                f"the configured dependency: {paths[0]}"
            )
        return ResolvedVideoReference(
            video_id=receipt.database_id,
            source_video_hash=receipt.published_content_sha256,
        )
    if transcode_job is not None:
        try:
            receipt = VideoTranscodeReceipt.model_validate_json(payload)
        except ValueError as exc:
            raise RuntimeError(
                f"Invalid video transcode receipt schema: {paths[0]}"
            ) from exc
        if receipt.job_id != transcode_job:
            raise RuntimeError(
                "Video transcode receipt job identity does not match "
                f"the configured dependency: {paths[0]}"
            )
        if receipt.status == "dry_run":
            raise RuntimeError(
                "A dry-run transcode receipt cannot authorize downstream HLS work."
            )
        return ResolvedVideoReference(
            video_id=receipt.video_id,
            processed_video_hash=receipt.published_processed_sha256,
        )
    raise RuntimeError("Upstream receipt has no configured stage identity.")


def resolve_video_reference(
    job: _VideoReference,
    upstream_paths: list[str],
) -> ResolvedVideoReference:
    if job.video_id is not None:
        if upstream_paths:
            raise RuntimeError("Direct video_id jobs cannot have an upstream receipt.")
        return ResolvedVideoReference(video_id=job.video_id)
    return read_upstream_video_reference(
        upstream_paths,
        import_job=job.import_job,
        transcode_job=job.transcode_job,
    )


def assert_video_reference_is_current(
    video: _PersistedVideoReference,
    reference: ResolvedVideoReference,
) -> None:
    if (
        reference.source_video_hash is not None
        and str(video.video_hash) != reference.source_video_hash
    ):
        raise RuntimeError(
            "Upstream import receipt does not match the current source generation."
        )
    if (
        reference.processed_video_hash is not None
        and str(video.processed_video_hash or "") != reference.processed_video_hash
    ):
        raise RuntimeError(
            "Upstream transcode receipt does not match the current processed "
            "video generation."
        )


def completed_receipt(
    *,
    job_id: str,
    media_type: Literal["video", "report"],
    preflight_source_sha256: str,
    database_id: int,
    published_content_sha256: str,
    retry_requested: bool,
    provenance: ReceiptProvenance,
) -> ImportReceipt:
    return ImportReceipt(
        job_id=job_id,
        media_type=media_type,
        preflight_source_sha256=preflight_source_sha256,
        database_id=database_id,
        published_content_sha256=published_content_sha256,
        retry_requested=retry_requested,
        **provenance.model_dump(),
    )


def receipt_provenance(
    *,
    batch_id: str | None,
    config_sha256: str | None,
    attempt: int,
    job_id: str,
    job_payload: object,
    started_at: datetime,
    duration_seconds: float,
) -> ReceiptProvenance:
    completed_at = datetime.now(timezone.utc)
    resolved_batch_id = batch_id or f"direct-{job_id}"
    if config_sha256 is None:
        encoded = json.dumps(
            job_payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        config_sha256 = hashlib.sha256(encoded).hexdigest()
    return ReceiptProvenance(
        batch_id=resolved_batch_id,
        attempt=attempt,
        config_sha256=config_sha256,
        started_at=started_at,
        completed_at=completed_at,
        duration_seconds=duration_seconds,
    )


def write_receipt(
    receipt: ImportReceipt | VideoTranscodeReceipt | VideoHlsReceipt,
    destination: Path,
) -> None:
    payload = f"{receipt.model_dump_json(indent=2)}\n".encode("utf-8")
    atomic_write_file(
        destination=destination,
        content=(payload,),
        required_bytes=len(payload),
        file_mode=0o600,
        dir_mode=0o700,
    )

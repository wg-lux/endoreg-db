from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from datetime import timedelta
from pathlib import Path
from typing import TypedDict, cast

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import models
from django.db.utils import OperationalError, ProgrammingError
from django.utils import timezone

from endoreg_db.config.env import (
    env_int,
    get_protected_media_root,
    get_protected_media_url,
)
from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.models.media.video.hls_artifact import VideoHlsArtifact
from endoreg_db.models.media.video.video_processing import VideoProcessingHistory
from endoreg_db.models.state.raw_pdf import RawPdfState
from endoreg_db.models.state.video import VideoState
from endoreg_db.services.audit_integrity import get_audit_ledger_integrity_status
from endoreg_db.services.environment_readiness import (
    ReadinessIssue,
    check_environment_readiness,
)
from endoreg_db.services.hub.deployment import (
    get_deployment_role,
    transfer_api_enabled,
)
from endoreg_db.services.jobs.stale_recovery import VIDEO_PROCESSING_STALE_TIMEOUT
from endoreg_db.utils.file_operations import atomic_write_file
from endoreg_db.utils.paths import (
    LOG_DIR,
    PROTECTED_DATA_ROOT,
    QUARANTINE_DIR,
    STORAGE_DIR,
)

SECRET_KEY_FINGERPRINT_FILE = LOG_DIR / ".secret_key_fingerprint"
DEFAULT_MIN_FREE_BYTES = 1024 * 1024 * 1024
DEFAULT_QUARANTINE_MAX_AGE_DAYS = 30
type _CommandOption = bool


class _AuditLedgerIntegrityStatus(TypedDict):
    status: str
    verified: bool
    checked_at: str | None
    entry_count: int | None
    error: str | None
    source: str


class _AnonymizationProcessingStats(TypedDict):
    failed_videos: int | None
    failed_reports: int | None
    stale_video_histories: int | None
    stale_timeout_seconds: int
    error: str | None


class _UploadSourceCleanupStats(TypedDict):
    failed: int | None
    stale_eligible: int | None
    stale_deleting: int | None
    ledger_mismatches: int | None
    unusually_large_blocked: int | None
    eligible_max_age_seconds: int
    deleting_max_age_seconds: int
    large_blocked_bytes: int
    error: str | None


def _secret_key_fingerprint() -> str:
    secret_key_file = os.environ.get("DJANGO_SECRET_KEY_FILE", "").strip()
    if secret_key_file:
        content = Path(secret_key_file).read_text(encoding="utf-8").strip()
    else:
        content = str(settings.SECRET_KEY)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _path_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _quarantine_stats(now: float) -> dict[str, int | float | None | str]:
    if not QUARANTINE_DIR.exists():
        return {
            "path": str(QUARANTINE_DIR),
            "count": 0,
            "bytes": 0,
            "oldest_age_seconds": None,
        }

    file_count = 0
    total_bytes = 0
    oldest_mtime: float | None = None
    for path in QUARANTINE_DIR.rglob("*"):
        if not path.is_file():
            continue
        stat_result = path.stat()
        file_count += 1
        total_bytes += stat_result.st_size
        if oldest_mtime is None or stat_result.st_mtime < oldest_mtime:
            oldest_mtime = stat_result.st_mtime

    return {
        "path": str(QUARANTINE_DIR),
        "count": file_count,
        "bytes": total_bytes,
        "oldest_age_seconds": None if oldest_mtime is None else now - oldest_mtime,
    }


def _upload_job_failure_stats() -> dict[str, int | str | None]:
    try:
        now = timezone.now()
        return {
            "failed": UploadJob.objects.filter(status=UploadJob.Status.ERROR).count(),
            "lost": UploadJob.objects.filter(status=UploadJob.Status.LOST).count(),
            "retrying": UploadJob.objects.filter(
                status=UploadJob.Status.RETRYING
            ).count(),
            "retry_due": UploadJob.objects.filter(
                status=UploadJob.Status.RETRYING,
                next_retry_at__lte=now,
            ).count(),
            "retry_exhausted": UploadJob.objects.filter(
                status=UploadJob.Status.ERROR,
                retry_count__gte=models.F("max_retries"),
                retry_count__gt=0,
            ).count(),
            "error": None,
        }
    except (OperationalError, ProgrammingError) as exc:
        return {
            "failed": None,
            "lost": None,
            "retrying": None,
            "retry_due": None,
            "retry_exhausted": None,
            "error": str(exc),
        }


def _upload_source_cleanup_stats() -> _UploadSourceCleanupStats:
    eligible_max_age_seconds = env_int(
        "ENDOREG_HEALTH_UPLOAD_SOURCE_ELIGIBLE_MAX_AGE_SECONDS",
        24 * 60 * 60,
    )
    deleting_max_age_seconds = env_int(
        "ENDOREG_HEALTH_UPLOAD_SOURCE_DELETING_MAX_AGE_SECONDS",
        60 * 60,
    )
    large_blocked_bytes = env_int(
        "ENDOREG_HEALTH_UPLOAD_SOURCE_LARGE_BLOCKED_BYTES",
        2 * 1024 * 1024 * 1024,
    )
    now = timezone.now()
    try:
        return {
            "failed": UploadJob.objects.filter(
                cleanup_failure_count__gt=0,
            )
            .exclude(
                cleanup_status=UploadJob.CleanupStatus.COMPLETED,
            )
            .count(),
            "stale_eligible": UploadJob.objects.filter(
                cleanup_status=UploadJob.CleanupStatus.ELIGIBLE,
                source_file_persisted=True,
                source_file_delete_eligible_at__lte=(
                    now - timedelta(seconds=eligible_max_age_seconds)
                ),
            ).count(),
            "stale_deleting": UploadJob.objects.filter(
                cleanup_status=UploadJob.CleanupStatus.DELETING,
                cleanup_started_at__lte=(
                    now - timedelta(seconds=deleting_max_age_seconds)
                ),
            ).count(),
            "ledger_mismatches": UploadJob.objects.filter(
                models.Q(
                    cleanup_status=UploadJob.CleanupStatus.COMPLETED,
                    source_file_persisted=True,
                )
                | models.Q(
                    cleanup_status=UploadJob.CleanupStatus.DELETING,
                    source_file_persisted=False,
                )
            ).count(),
            "unusually_large_blocked": UploadJob.objects.filter(
                cleanup_status=UploadJob.CleanupStatus.DELETING,
                cleanup_last_error_code__gt="",
                cleanup_source_size_bytes__gte=large_blocked_bytes,
            ).count(),
            "eligible_max_age_seconds": eligible_max_age_seconds,
            "deleting_max_age_seconds": deleting_max_age_seconds,
            "large_blocked_bytes": large_blocked_bytes,
            "error": None,
        }
    except (OperationalError, ProgrammingError) as exc:
        return {
            "failed": None,
            "stale_eligible": None,
            "stale_deleting": None,
            "ledger_mismatches": None,
            "unusually_large_blocked": None,
            "eligible_max_age_seconds": eligible_max_age_seconds,
            "deleting_max_age_seconds": deleting_max_age_seconds,
            "large_blocked_bytes": large_blocked_bytes,
            "error": str(exc),
        }


def _hls_materialization_stats() -> dict[str, int | str | None]:
    stale_before = timezone.now() - VIDEO_PROCESSING_STALE_TIMEOUT
    try:
        return {
            "queued": VideoHlsArtifact.objects.filter(
                status=VideoHlsArtifact.Status.QUEUED
            ).count(),
            "materializing": VideoHlsArtifact.objects.filter(
                status=VideoHlsArtifact.Status.MATERIALIZING
            ).count(),
            "validated": VideoHlsArtifact.objects.filter(
                status=VideoHlsArtifact.Status.VALIDATED
            ).count(),
            "ready": VideoHlsArtifact.objects.filter(
                status=VideoHlsArtifact.Status.READY
            ).count(),
            "superseded": VideoHlsArtifact.objects.filter(
                status=VideoHlsArtifact.Status.SUPERSEDED
            ).count(),
            "failed": VideoHlsArtifact.objects.filter(
                status=VideoHlsArtifact.Status.FAILED
            ).count(),
            "stale_in_flight": VideoHlsArtifact.objects.filter(
                status__in=(
                    VideoHlsArtifact.Status.QUEUED,
                    VideoHlsArtifact.Status.MATERIALIZING,
                    VideoHlsArtifact.Status.VALIDATED,
                ),
                updated_at__lte=stale_before,
            ).count(),
            "error": None,
        }
    except (OperationalError, ProgrammingError) as exc:
        return {
            "queued": None,
            "materializing": None,
            "ready": None,
            "failed": None,
            "stale_in_flight": None,
            "error": str(exc),
        }


def _anonymization_processing_stats() -> _AnonymizationProcessingStats:
    """Return unresolved failures and stale processing visible to operators."""
    stale_before = timezone.now() - VIDEO_PROCESSING_STALE_TIMEOUT
    try:
        return {
            "failed_videos": VideoState.objects.filter(processing_error=True).count(),
            "failed_reports": RawPdfState.objects.filter(processing_error=True).count(),
            "stale_video_histories": VideoProcessingHistory.objects.filter(
                status__in=(
                    VideoProcessingHistory.STATUS_PENDING,
                    VideoProcessingHistory.STATUS_RUNNING,
                ),
                created_at__lte=stale_before,
            ).count(),
            "stale_timeout_seconds": int(
                VIDEO_PROCESSING_STALE_TIMEOUT.total_seconds()
            ),
            "error": None,
        }
    except (OperationalError, ProgrammingError) as exc:
        return {
            "failed_videos": None,
            "failed_reports": None,
            "stale_video_histories": None,
            "stale_timeout_seconds": int(
                VIDEO_PROCESSING_STALE_TIMEOUT.total_seconds()
            ),
            "error": str(exc),
        }


def _audit_ledger_integrity_status() -> _AuditLedgerIntegrityStatus:
    try:
        return cast(
            _AuditLedgerIntegrityStatus,
            get_audit_ledger_integrity_status(),
        )
    except (OperationalError, ProgrammingError) as exc:
        return {
            "status": "error",
            "verified": False,
            "checked_at": None,
            "entry_count": None,
            "error": str(exc),
            "source": "health_check",
        }


def _storage_free_stats() -> dict[str, int | float | str | None]:
    try:
        usage = shutil.disk_usage(STORAGE_DIR)
    except OSError as exc:
        return {
            "path": str(STORAGE_DIR.resolve()),
            "total_bytes": None,
            "used_bytes": None,
            "free_bytes": None,
            "free_ratio": 0.0,
            "error": str(exc),
        }
    return {
        "path": str(STORAGE_DIR.resolve()),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "free_ratio": usage.free / usage.total if usage.total else 0.0,
        "error": None,
    }


def _initialize_secret_key_fingerprint() -> tuple[str, str]:
    secret_fingerprint = _secret_key_fingerprint()
    if SECRET_KEY_FINGERPRINT_FILE.exists():
        previous_fingerprint = SECRET_KEY_FINGERPRINT_FILE.read_text(
            encoding="utf-8"
        ).strip()
        return secret_fingerprint, previous_fingerprint

    atomic_write_file(
        destination=SECRET_KEY_FINGERPRINT_FILE,
        content=[secret_fingerprint.encode("utf-8")],
        file_mode=0o640,
        dir_mode=0o750,
    )
    return secret_fingerprint, ""


def _base_health_checks(
    *,
    protected_media_url: str,
    protected_media_root: Path,
    current_gid: int,
    supplemental_gids: set[int],
    secret_fingerprint: str,
    previous_fingerprint: str,
) -> dict[str, bool]:
    protected_media_root_exists = protected_media_root.exists()
    media_gid = (
        protected_media_root.stat().st_gid if protected_media_root_exists else -1
    )
    return {
        "protected_media_url_reachable": protected_media_url == "/protected_media/",
        "protected_media_root_exists": protected_media_root_exists,
        "protected_media_root_within_protected_data": _path_within(
            PROTECTED_DATA_ROOT, protected_media_root
        ),
        "app_user_has_media_gid": (
            protected_media_root_exists
            and (current_gid == media_gid or media_gid in supplemental_gids)
        ),
        "secret_key_stable": (
            not previous_fingerprint or previous_fingerprint == secret_fingerprint
        ),
    }


def _storage_free_above_threshold(
    free_bytes: int | float | str | None,
    min_free_bytes: int,
) -> bool:
    return free_bytes is not None and int(free_bytes) >= min_free_bytes


def _quarantine_age_under_threshold(
    oldest_age_seconds: int | float | None | str,
    max_age_seconds: int,
) -> bool:
    if oldest_age_seconds is None:
        return True
    return (
        isinstance(oldest_age_seconds, (int, float))
        and oldest_age_seconds <= max_age_seconds
    )


def _audit_ledger_is_verified(
    audit_ledger_integrity: _AuditLedgerIntegrityStatus,
) -> bool:
    return (
        audit_ledger_integrity.get("status") == "verified"
        and audit_ledger_integrity.get("verified") is True
    )


def _local_study_server_checks(
    *,
    upload_jobs: dict[str, int | str | None],
    hls_materializations: dict[str, int | str | None],
    anonymization_processing: _AnonymizationProcessingStats,
    upload_source_cleanup: _UploadSourceCleanupStats,
    storage_free: dict[str, int | float | str | None],
    audit_ledger_integrity: _AuditLedgerIntegrityStatus,
    oldest_quarantine_age_seconds: int | float | None | str,
    min_free_bytes: int,
    max_quarantine_age_seconds: int,
) -> dict[str, bool]:
    return {
        "local_study_server_transfer_api_disabled": not transfer_api_enabled(),
        "local_study_server_no_failed_upload_jobs": upload_jobs["failed"] == 0,
        "local_study_server_no_lost_upload_jobs": upload_jobs["lost"] == 0,
        "local_study_server_no_exhausted_upload_retries": (
            upload_jobs["retry_exhausted"] == 0
        ),
        "local_study_server_no_failed_hls_materialization": (
            hls_materializations["failed"] == 0
        ),
        "local_study_server_no_stale_hls_materialization": (
            hls_materializations["stale_in_flight"] == 0
        ),
        "local_study_server_no_failed_anonymization": (
            anonymization_processing["failed_videos"] == 0
            and anonymization_processing["failed_reports"] == 0
        ),
        "local_study_server_no_stale_video_processing": (
            anonymization_processing["stale_video_histories"] == 0
        ),
        "local_study_server_no_failed_upload_source_cleanup": (
            upload_source_cleanup["failed"] == 0
        ),
        "local_study_server_no_stale_eligible_upload_sources": (
            upload_source_cleanup["stale_eligible"] == 0
        ),
        "local_study_server_no_stale_deleting_upload_sources": (
            upload_source_cleanup["stale_deleting"] == 0
        ),
        "local_study_server_no_upload_source_ledger_mismatch": (
            upload_source_cleanup["ledger_mismatches"] == 0
        ),
        "local_study_server_no_unusually_large_blocked_upload_sources": (
            upload_source_cleanup["unusually_large_blocked"] == 0
        ),
        "local_study_server_storage_free_above_threshold": _storage_free_above_threshold(
            storage_free["free_bytes"],
            min_free_bytes,
        ),
        "local_study_server_quarantine_age_under_threshold": (
            _quarantine_age_under_threshold(
                oldest_quarantine_age_seconds,
                max_quarantine_age_seconds,
            )
        ),
        "local_study_server_audit_ledger_integrity_verified": (
            _audit_ledger_is_verified(audit_ledger_integrity)
        ),
    }


def _emit_health_check_results(
    command: BaseCommand,
    checks: dict[str, bool],
) -> None:
    for key, value in checks.items():
        marker = command.style.SUCCESS("ok") if value else command.style.ERROR("fail")
        command.stdout.write(f"{marker} {key}")


def _emit_readiness_issues(
    command: BaseCommand,
    readiness_issues: list[ReadinessIssue],
) -> None:
    for issue in readiness_issues:
        marker = (
            command.style.ERROR("fail")
            if issue.severity == "critical"
            else command.style.WARNING("warn")
        )
        path_suffix = f" ({issue.path})" if issue.path else ""
        command.stdout.write(f"{marker} {issue.code}: {issue.message}{path_suffix}")


def _failed_health_checks(
    checks: dict[str, bool],
    readiness_issues: list[ReadinessIssue],
) -> list[str]:
    failed = [key for key, value in checks.items() if not value]
    failed.extend(
        issue.code for issue in readiness_issues if issue.severity == "critical"
    )
    return failed


class Command(BaseCommand):
    help = (
        "Validate the LuxNix runtime contract for protected media, group access, "
        "and stable SECRET_KEY derivation."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit the health report as JSON.",
        )

    def handle(self, *args: str, **options: _CommandOption) -> None:
        protected_media_url = get_protected_media_url()
        protected_media_root = get_protected_media_root().resolve()
        secret_fingerprint, previous_fingerprint = _initialize_secret_key_fingerprint()
        deployment_role = get_deployment_role()
        local_study_server = deployment_role == "local_study_server"
        now = time.time()
        min_free_bytes = env_int(
            "ENDOREG_HEALTH_MIN_FREE_BYTES",
            DEFAULT_MIN_FREE_BYTES,
        )
        max_quarantine_age_days = env_int(
            "ENDOREG_HEALTH_QUARANTINE_MAX_AGE_DAYS",
            DEFAULT_QUARANTINE_MAX_AGE_DAYS,
        )
        quarantine = _quarantine_stats(now)
        upload_jobs = _upload_job_failure_stats()
        hls_materializations = _hls_materialization_stats()
        anonymization_processing = _anonymization_processing_stats()
        upload_source_cleanup = _upload_source_cleanup_stats()
        storage_free = _storage_free_stats()
        audit_ledger_integrity = _audit_ledger_integrity_status()
        oldest_age_seconds = quarantine["oldest_age_seconds"]
        max_quarantine_age_seconds = max_quarantine_age_days * 24 * 60 * 60

        checks = _base_health_checks(
            protected_media_url=protected_media_url,
            protected_media_root=protected_media_root,
            current_gid=os.getgid(),
            supplemental_gids=set(os.getgroups()),
            secret_fingerprint=secret_fingerprint,
            previous_fingerprint=previous_fingerprint,
        )
        if local_study_server:
            checks.update(
                _local_study_server_checks(
                    upload_jobs=upload_jobs,
                    hls_materializations=hls_materializations,
                    anonymization_processing=anonymization_processing,
                    upload_source_cleanup=upload_source_cleanup,
                    storage_free=storage_free,
                    audit_ledger_integrity=audit_ledger_integrity,
                    oldest_quarantine_age_seconds=oldest_age_seconds,
                    min_free_bytes=min_free_bytes,
                    max_quarantine_age_seconds=max_quarantine_age_seconds,
                )
            )
        readiness_issues = check_environment_readiness()
        payload = {
            "checks": checks,
            "readiness_issues": [
                {
                    "severity": issue.severity,
                    "code": issue.code,
                    "message": issue.message,
                    "path": issue.path,
                }
                for issue in readiness_issues
            ],
            "protected_media_root": str(protected_media_root),
            "protected_media_url": protected_media_url,
            "storage_root": str(STORAGE_DIR.resolve()),
            "secret_key_fingerprint": secret_fingerprint,
            "deployment_role": deployment_role,
            "local_study_server": {
                "enabled": local_study_server,
                "transfer_api_enabled": transfer_api_enabled(),
                "audit_ledger_integrity": audit_ledger_integrity,
                "quarantine": quarantine,
                "upload_jobs": upload_jobs,
                "hls_materializations": hls_materializations,
                "anonymization_processing": anonymization_processing,
                "upload_source_cleanup": upload_source_cleanup,
                "storage_free": storage_free,
                "min_free_bytes": min_free_bytes,
                "max_quarantine_age_days": max_quarantine_age_days,
            },
        }

        emit_json = options["json"]
        if emit_json:
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
        else:
            _emit_health_check_results(self, checks)
            _emit_readiness_issues(self, readiness_issues)

        failed = _failed_health_checks(checks, readiness_issues)
        if failed:
            raise CommandError(f"System health check failed: {', '.join(failed)}")

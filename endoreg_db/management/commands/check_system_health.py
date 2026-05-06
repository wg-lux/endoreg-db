from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.utils import OperationalError, ProgrammingError

from endoreg_db.config.env import (
    env_int,
    get_protected_media_root,
    get_protected_media_url,
)
from endoreg_db.models import UploadJob
from endoreg_db.services.audit_integrity import get_audit_ledger_integrity_status
from endoreg_db.services.environment_readiness import check_environment_readiness
from endoreg_db.services.hub.deployment import (
    get_deployment_role,
    transfer_api_enabled,
)
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
        return {
            "failed": UploadJob.objects.filter(status=UploadJob.Status.ERROR).count(),
            "lost": UploadJob.objects.filter(status=UploadJob.Status.LOST).count(),
            "error": None,
        }
    except (OperationalError, ProgrammingError) as exc:
        return {
            "failed": None,
            "lost": None,
            "error": str(exc),
        }


def _audit_ledger_integrity_status() -> dict[str, object]:
    try:
        return get_audit_ledger_integrity_status()
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


class Command(BaseCommand):
    help = (
        "Validate the LuxNix runtime contract for protected media, group access, "
        "and stable SECRET_KEY derivation."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit the health report as JSON.",
        )

    def handle(self, *args, **options) -> None:
        protected_media_url = get_protected_media_url()
        protected_media_root = get_protected_media_root().resolve()
        current_gid = os.getgid()
        supplemental_gids = set(os.getgroups())
        media_gid = (
            protected_media_root.stat().st_gid if protected_media_root.exists() else -1
        )
        secret_fingerprint = _secret_key_fingerprint()

        previous_fingerprint = ""
        if SECRET_KEY_FINGERPRINT_FILE.exists():
            previous_fingerprint = SECRET_KEY_FINGERPRINT_FILE.read_text(
                encoding="utf-8"
            ).strip()
        else:
            atomic_write_file(
                destination=SECRET_KEY_FINGERPRINT_FILE,
                content=[secret_fingerprint.encode("utf-8")],
                file_mode=0o640,
                dir_mode=0o750,
            )

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
        storage_free = _storage_free_stats()
        audit_ledger_integrity = _audit_ledger_integrity_status()
        oldest_age_seconds = quarantine["oldest_age_seconds"]
        max_quarantine_age_seconds = max_quarantine_age_days * 24 * 60 * 60

        checks = {
            "protected_media_url_reachable": protected_media_url == "/protected_media/",
            "protected_media_root_exists": protected_media_root.exists(),
            "protected_media_root_within_protected_data": _path_within(
                PROTECTED_DATA_ROOT, protected_media_root
            ),
            "app_user_has_media_gid": (
                protected_media_root.exists()
                and (current_gid == media_gid or media_gid in supplemental_gids)
            ),
            "secret_key_stable": (
                not previous_fingerprint or previous_fingerprint == secret_fingerprint
            ),
        }
        if local_study_server:
            checks.update(
                {
                    "local_study_server_transfer_api_disabled": (
                        not transfer_api_enabled()
                    ),
                    "local_study_server_no_failed_upload_jobs": (
                        upload_jobs["failed"] == 0
                    ),
                    "local_study_server_no_lost_upload_jobs": (
                        upload_jobs["lost"] == 0
                    ),
                    "local_study_server_storage_free_above_threshold": (
                        storage_free["free_bytes"] is not None
                        and int(storage_free["free_bytes"]) >= min_free_bytes
                    ),
                    "local_study_server_quarantine_age_under_threshold": (
                        oldest_age_seconds is None
                        or (
                            isinstance(oldest_age_seconds, (int, float))
                            and oldest_age_seconds <= max_quarantine_age_seconds
                        )
                    ),
                    "local_study_server_audit_ledger_integrity_verified": (
                        audit_ledger_integrity.get("status") == "verified"
                        and audit_ledger_integrity.get("verified") is True
                    ),
                }
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
                "storage_free": storage_free,
                "min_free_bytes": min_free_bytes,
                "max_quarantine_age_days": max_quarantine_age_days,
            },
        }

        if options["json"]:
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
        else:
            for key, value in checks.items():
                marker = self.style.SUCCESS("ok") if value else self.style.ERROR("fail")
                self.stdout.write(f"{marker} {key}")
            for issue in readiness_issues:
                marker = (
                    self.style.ERROR("fail")
                    if issue.severity == "critical"
                    else self.style.WARNING("warn")
                )
                path_suffix = f" ({issue.path})" if issue.path else ""
                self.stdout.write(
                    f"{marker} {issue.code}: {issue.message}{path_suffix}"
                )

        failed = [key for key, value in checks.items() if not value]
        failed.extend(
            issue.code for issue in readiness_issues if issue.severity == "critical"
        )
        if failed:
            raise CommandError(f"System health check failed: {', '.join(failed)}")

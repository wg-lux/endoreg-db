from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from endoreg_db.config.env import (
    get_media_url,
    get_protected_media_root,
    get_protected_media_url,
)

from endoreg_db.utils.paths import (
    get_runtime_paths,
    ensure_within_protected_media_root,
    ensure_within_storage_root,
    ensure_within_runtime_root,
)
from endoreg_db.utils.rust_backend import has_native_capability


@dataclass(frozen=True)
class ReadinessIssue:
    severity: str
    code: str
    message: str
    path: str | None = None


def _check_directory_access(path: Path, *, code_prefix: str) -> list[ReadinessIssue]:
    issues: list[ReadinessIssue] = []
    resolved = path.resolve()
    resolved_str = str(resolved)
    if not os.path.exists(resolved_str):
        issues.append(
            ReadinessIssue(
                severity="critical",
                code=f"{code_prefix}_missing",
                message=f"Required directory does not exist: {resolved}",
                path=str(resolved),
            )
        )
        return issues
    if not os.path.isdir(resolved_str):
        issues.append(
            ReadinessIssue(
                severity="critical",
                code=f"{code_prefix}_not_dir",
                message=f"Required path is not a directory: {resolved}",
                path=str(resolved),
            )
        )
        return issues
    for mode, suffix in ((os.R_OK, "read"), (os.W_OK, "write"), (os.X_OK, "execute")):
        if not os.access(resolved_str, mode):
            issues.append(
                ReadinessIssue(
                    severity="critical",
                    code=f"{code_prefix}_{suffix}_denied",
                    message=f"Missing {suffix} permission on required directory: {resolved}",
                    path=str(resolved),
                )
            )
    return issues


def _check_protected_media_contract() -> list[ReadinessIssue]:
    issues: list[ReadinessIssue] = []
    protected_media_url = get_protected_media_url()
    media_url = get_media_url()
    protected_media_root = get_protected_media_root().resolve()

    if protected_media_root != get_runtime_paths().storage:
        issues.append(
            ReadinessIssue(
                severity="critical",
                code="protected_media_root_mismatch",
                message="Protected media root must equal canonical runtime storage.",
                path=str(protected_media_root),
            )
        )

    if protected_media_url != "/protected_media/":
        issues.append(
            ReadinessIssue(
                severity="critical",
                code="protected_media_url_invalid",
                message=(
                    "Protected media must be mounted at /protected_media/ for the "
                    "LuxNix/Nginx contract."
                ),
                path=protected_media_url,
            )
        )

    if media_url and media_url.startswith("/media/"):
        issues.append(
            ReadinessIssue(
                severity="critical",
                code="media_url_public_mount",
                message="Protected media must not be exposed under /media/.",
                path=media_url,
            )
        )

    if media_url and media_url != protected_media_url:
        issues.append(
            ReadinessIssue(
                severity="critical",
                code="media_url_mismatch",
                message=(
                    "MEDIA_URL must match the protected media URL for protected "
                    "payload delivery."
                ),
                path=media_url,
            )
        )

    try:
        ensure_within_protected_media_root(protected_media_root)
    except ValueError:
        issues.append(
            ReadinessIssue(
                severity="critical",
                code="protected_media_root_outside_storage_root",
                message=("Protected media root must remain inside canonical storage."),
                path=str(protected_media_root),
            )
        )

    return issues


def _check_report_native_snapshot_contract() -> list[ReadinessIssue]:
    if not bool(getattr(settings, "REPORT_IMPORT_REQUIRE_NATIVE_SNAPSHOT", False)):
        return []
    if has_native_capability(
        "report_source_snapshot",
        "report_source_snapshot_v1",
    ):
        return []
    return [
        ReadinessIssue(
            severity="critical",
            code="report_native_snapshot_unavailable",
            message=(
                "The production report-import profile requires native capability "
                "report_source_snapshot_v1, but the loaded extension does not "
                "advertise it."
            ),
        )
    ]


def _check_runtime_topology_contract() -> list[ReadinessIssue]:
    issues: list[ReadinessIssue] = []
    paths = get_runtime_paths()

    runtime_paths = {
        "runtime_root": paths.runtime_root,
        "storage_root": paths.storage,
        "terminology_root": paths.terminology,
        "watcher_video_drop": paths.watcher_video_drop,
        "watcher_report_drop": paths.watcher_report_drop,
        "watcher_preanonymized_drop": paths.watcher_preanonymized_drop,
    }

    for label, path in runtime_paths.items():
        try:
            ensure_within_runtime_root(path)
        except ValueError as exc:
            issues.append(
                ReadinessIssue(
                    severity="critical",
                    code=f"{label}_outside_runtime_root",
                    message=str(exc),
                    path=str(path.resolve()),
                )
            )

    storage_paths = {
        "storage_root": paths.storage,
        "streamable_video_root": paths.streamable_videos_root,
        "streamable_raw_root": paths.streamable_videos_raw_media,
        "streamable_processed_root": paths.streamable_videos_processed_media,
    }

    for label, path in storage_paths.items():
        try:
            ensure_within_storage_root(path)
        except ValueError as exc:
            issues.append(
                ReadinessIssue(
                    severity="critical",
                    code=f"{label}_outside_storage_root",
                    message=str(exc),
                    path=str(path.resolve()),
                )
            )

    return issues


def check_environment_readiness() -> list[ReadinessIssue]:
    paths = get_runtime_paths()
    issues: list[ReadinessIssue] = []

    issues.extend(_check_runtime_topology_contract())
    issues.extend(_check_protected_media_contract())
    issues.extend(_check_report_native_snapshot_contract())

    required_directories = {
        "runtime_root": paths.runtime_root,
        "storage_root": paths.storage,
        "terminology_root": paths.terminology,
        "watcher_video_drop": paths.watcher_video_drop,
        "watcher_report_drop": paths.watcher_report_drop,
        "watcher_preanonymized_drop": paths.watcher_preanonymized_drop,
        "streamable_video_root": paths.streamable_videos_root,
        "streamable_raw_root": paths.streamable_videos_raw_media,
        "streamable_processed_root": paths.streamable_videos_processed_media,
    }

    for code_prefix, path in required_directories.items():
        issues.extend(
            _check_directory_access(
                path,
                code_prefix=code_prefix,
            )
        )

    return issues


def assert_environment_readiness() -> None:
    issues = check_environment_readiness()
    critical_issues = [issue for issue in issues if issue.severity == "critical"]
    if critical_issues:
        lines = [f"{issue.code}: {issue.message}" for issue in critical_issues]
        raise RuntimeError("Environment readiness checks failed:\n" + "\n".join(lines))

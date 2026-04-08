from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from endoreg_db.services.streamable_media import (
    STREAMABLE_PROCESSED_VIDEO_ROOT,
    STREAMABLE_RAW_VIDEO_ROOT,
    STREAMABLE_VIDEO_ROOT,
)
from endoreg_db.utils.paths import (
    IO_DIR,
    PROTECTED_DATA_ROOT,
    STORAGE_DIR,
    WATCHER_PREANONYMIZED_DROP_DIR,
    WATCHER_REPORT_DROP_DIR,
    WATCHER_VIDEO_DROP_DIR,
)


@dataclass(frozen=True)
class ReadinessIssue:
    severity: str
    code: str
    message: str
    path: str | None = None


def _path_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


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


def _check_same_filesystem(
    source: Path, target: Path, *, code: str
) -> list[ReadinessIssue]:
    try:
        source_stat = source.stat()
        target_stat = target.stat()
    except FileNotFoundError:
        return []
    except OSError as exc:
        return [
            ReadinessIssue(
                severity="critical",
                code=f"{code}_stat_failed",
                message=f"Could not stat filesystem for {source} or {target}: {exc}",
            )
        ]
    same_device = source_stat.st_dev == target_stat.st_dev
    if same_device:
        return []
    return [
        ReadinessIssue(
            severity="warning",
            code=f"{code}_cross_filesystem",
            message=(
                f"Atomic move is not guaranteed between {source} and {target}; "
                "the paths are on different filesystems."
            ),
        )
    ]


def _check_protected_media_contract() -> list[ReadinessIssue]:
    issues: list[ReadinessIssue] = []
    protected_media_url = (
        os.environ.get("NGINX_PROTECTED_MEDIA_URL", "/protected_media/").strip()
        or "/protected_media/"
    )
    media_url = os.environ.get("MEDIA_URL", protected_media_url).strip()
    protected_media_root = Path(
        os.environ.get("PROTECTED_MEDIA_ROOT", str(STORAGE_DIR))
    ).resolve()

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

    if media_url == "/media/" or media_url.startswith("/media/"):
        issues.append(
            ReadinessIssue(
                severity="critical",
                code="media_url_public_mount",
                message="Protected media must not be exposed under /media/.",
                path=media_url,
            )
        )
    elif media_url and media_url != protected_media_url:
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

    if not _path_within(PROTECTED_DATA_ROOT, protected_media_root):
        issues.append(
            ReadinessIssue(
                severity="critical",
                code="protected_media_root_outside_protected_root",
                message=(
                    "Protected media root must remain inside the protected runtime "
                    "root."
                ),
                path=str(protected_media_root),
            )
        )

    return issues


def check_environment_readiness() -> list[ReadinessIssue]:
    issues: list[ReadinessIssue] = []
    issues.extend(_check_protected_media_contract())
    issues.extend(
        _check_directory_access(PROTECTED_DATA_ROOT, code_prefix="protected_root")
    )
    issues.extend(_check_directory_access(STORAGE_DIR, code_prefix="storage_root"))
    issues.extend(_check_directory_access(IO_DIR, code_prefix="io_root"))
    issues.extend(
        _check_directory_access(
            WATCHER_VIDEO_DROP_DIR, code_prefix="watcher_video_drop"
        )
    )
    issues.extend(
        _check_directory_access(
            WATCHER_REPORT_DROP_DIR, code_prefix="watcher_report_drop"
        )
    )
    issues.extend(
        _check_directory_access(
            WATCHER_PREANONYMIZED_DROP_DIR,
            code_prefix="watcher_preanonymized_drop",
        )
    )
    issues.extend(
        _check_directory_access(
            STREAMABLE_VIDEO_ROOT, code_prefix="streamable_video_root"
        )
    )
    issues.extend(
        _check_directory_access(
            STREAMABLE_RAW_VIDEO_ROOT,
            code_prefix="streamable_raw_root",
        )
    )
    issues.extend(
        _check_directory_access(
            STREAMABLE_PROCESSED_VIDEO_ROOT,
            code_prefix="streamable_processed_root",
        )
    )
    issues.extend(
        _check_same_filesystem(
            STORAGE_DIR,
            STREAMABLE_VIDEO_ROOT,
            code="streamable_atomic_move",
        )
    )
    return issues


def assert_environment_readiness() -> None:
    issues = check_environment_readiness()
    critical_issues = [issue for issue in issues if issue.severity == "critical"]
    if critical_issues:
        lines = [f"{issue.code}: {issue.message}" for issue in critical_issues]
        raise RuntimeError("Environment readiness checks failed:\n" + "\n".join(lines))

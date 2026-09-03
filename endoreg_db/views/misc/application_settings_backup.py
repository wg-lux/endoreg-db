from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from lx_dtypes.models.contracts.application_settings import (
    ApplicationSettingsBackupSourcePayload,
    ApplicationSettingsBackupStatusPayload,
)
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.request import Request
from rest_framework.response import Response

from endoreg_db.utils.file_operations import (
    atomic_copy_file,
    atomic_write_file,
    ensure_directory,
)
from endoreg_db.utils.paths import PROTECTED_DATA_ROOT, STORAGE_DIR
from endoreg_db.utils.permissions import EnvironmentAwarePermission


def _request_payload(data: object) -> dict[str, Any]:
    return cast(dict[str, Any], data) if isinstance(data, dict) else {}


def _required_backup_sources() -> list[Path]:
    sources: list[Path] = []
    for path in (PROTECTED_DATA_ROOT, STORAGE_DIR):
        if path not in sources:
            sources.append(path)
    return sources


def required_backup_sources() -> list[Path]:
    return _required_backup_sources()


def _count_files(root: Path) -> int:
    return sum(1 for path in root.rglob("*") if path.is_file())


def _backup_source_label(index: int, path: Path) -> str:
    if path == PROTECTED_DATA_ROOT:
        return "protected_root"
    if path == STORAGE_DIR:
        return "storage"
    if index == 0:
        return "storage"
    if index == 1:
        return "io"
    return f"source_{index + 1}"


def _backup_status_payload() -> ApplicationSettingsBackupStatusPayload:
    required_sources = [path.resolve() for path in _required_backup_sources()]
    missing_paths = [str(path) for path in required_sources if not path.exists()]
    source_roots = [
        ApplicationSettingsBackupSourcePayload(
            label=_backup_source_label(index, path),
            path=str(path),
            exists=path.exists(),
            file_count=_count_files(path) if path.exists() else 0,
        )
        for index, path in enumerate(required_sources)
    ]
    return ApplicationSettingsBackupStatusPayload(
        ready=len(missing_paths) == 0,
        missing_paths=missing_paths,
        required_path_count=len(required_sources),
        available_path_count=len(required_sources) - len(missing_paths),
        source_roots=source_roots,
    )


def _copy_backup_source_tree(source_root: Path, destination_root: Path) -> int:
    ensure_directory(destination_root)
    copied_count = 0
    for source_path in source_root.rglob("*"):
        relative_path = source_path.relative_to(source_root)
        destination_path = destination_root / relative_path
        if source_path.is_dir():
            ensure_directory(destination_path)
            continue
        if not source_path.is_file():
            continue
        atomic_copy_file(source=source_path, destination=destination_path)
        copied_count += 1
    return copied_count


def _resolve_backup_target(data: object) -> tuple[Path | None, Response | None]:
    target_path_raw = str(_request_payload(data).get("target_path", "") or "").strip()
    if not target_path_raw:
        return None, Response(
            {"errors": {"target_path": "target_path is required."}},
            status=status.HTTP_400_BAD_REQUEST,
        )
    target_root = Path(target_path_raw).expanduser()
    if not target_root.is_absolute():
        return None, Response(
            {"errors": {"target_path": "target_path must be absolute."}},
            status=status.HTTP_400_BAD_REQUEST,
        )
    resolved_target_root = target_root.resolve(strict=False)
    source_roots = [path.resolve() for path in _required_backup_sources()]
    if any(
        resolved_target_root == source_root
        or source_root in resolved_target_root.parents
        for source_root in source_roots
    ):
        return None, Response(
            {
                "errors": {
                    "target_path": "target_path must not be inside the live data roots."
                }
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    return resolved_target_root, None


def _copy_backup(
    backup_status: ApplicationSettingsBackupStatusPayload,
    target_root: Path,
) -> tuple[Path, list[dict[str, Any]]]:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_root = target_root / f"lx-annotate-backup-{timestamp}"
    if backup_root.exists():
        raise FileExistsError(backup_root)
    ensure_directory(backup_root)

    copied_roots: list[dict[str, Any]] = []
    for entry in backup_status.source_roots:
        source_path = Path(entry.path)
        destination = backup_root / entry.label
        copied_count = _copy_backup_source_tree(source_path, destination)
        copied_roots.append(
            {
                "label": entry.label,
                "source_path": str(source_path),
                "destination_path": str(destination),
                "file_count": copied_count,
            }
        )
    manifest = {
        "created_at": datetime.now().isoformat(),
        "target_root": str(backup_root),
        "copied_roots": copied_roots,
    }
    manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
    atomic_write_file(
        destination=backup_root / "manifest.json",
        content=[manifest_bytes],
        required_bytes=len(manifest_bytes),
    )
    return backup_root, copied_roots


@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_backup(request: Request) -> Response:
    backup_status = _backup_status_payload()
    if not backup_status.ready:
        return Response(
            {
                "detail": "Backup sources are incomplete.",
                "backup_status": backup_status.model_dump(mode="python"),
            },
            status=status.HTTP_409_CONFLICT,
        )
    target_root, error = _resolve_backup_target(request.data)
    if error is not None:
        return error
    assert target_root is not None
    try:
        backup_root, copied_roots = _copy_backup(backup_status, target_root)
    except FileExistsError:
        return Response(
            {"detail": "Backup target already exists."},
            status=status.HTTP_409_CONFLICT,
        )
    except OSError as exc:
        return Response(
            {"detail": f"Backup failed: {exc}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return Response(
        {
            "target_root": str(backup_root),
            "copied_roots": copied_roots,
        },
        status=status.HTTP_201_CREATED,
    )


__all__ = ["application_settings_backup"]

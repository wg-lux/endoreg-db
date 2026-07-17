from __future__ import annotations

import json
from collections.abc import Mapping
from contextlib import ExitStack
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from typing import Any, cast

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from endoreg_db.management.commands import check_system_health as health_command


def _prepare_health_paths(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    protected_root = tmp_path / "protected"
    protected_media_root = protected_root / "media"
    storage_root = protected_root / "storage"
    quarantine_root = protected_root / "quarantine"
    protected_media_root.mkdir(parents=True)
    storage_root.mkdir()
    quarantine_root.mkdir()
    return protected_root, protected_media_root, storage_root, quarantine_root


def _health_command_patches(
    *,
    tmp_path: Path,
    audit_status: Mapping[str, object],
):
    protected_root, protected_media_root, storage_root, quarantine_root = (
        _prepare_health_paths(tmp_path)
    )
    return [
        patch.object(health_command, "PROTECTED_DATA_ROOT", protected_root),
        patch.object(health_command, "STORAGE_DIR", storage_root),
        patch.object(health_command, "QUARANTINE_DIR", quarantine_root),
        patch.object(
            health_command,
            "SECRET_KEY_FINGERPRINT_FILE",
            tmp_path / "logs" / ".secret_key_fingerprint",
        ),
        patch.object(
            health_command,
            "get_protected_media_root",
            return_value=protected_media_root,
        ),
        patch.object(
            health_command,
            "get_protected_media_url",
            return_value="/protected_media/",
        ),
        patch.object(
            health_command, "get_deployment_role", return_value="local_study_server"
        ),
        patch.object(health_command, "transfer_api_enabled", return_value=False),
        patch.object(health_command, "check_environment_readiness", return_value=[]),
        patch.object(
            health_command,
            "_upload_job_failure_stats",
            return_value={"failed": 0, "lost": 0, "error": None},
        ),
        patch.object(
            health_command,
            "_anonymization_processing_stats",
            return_value={
                "failed_videos": 0,
                "failed_reports": 0,
                "stale_video_histories": 0,
                "stale_timeout_seconds": 7 * 60 * 60,
                "error": None,
            },
        ),
        patch.object(
            health_command,
            "_storage_free_stats",
            return_value={
                "path": str(storage_root),
                "total_bytes": 4 * 1024 * 1024 * 1024,
                "used_bytes": 1024,
                "free_bytes": 3 * 1024 * 1024 * 1024,
                "free_ratio": 0.75,
            },
        ),
        patch.object(
            health_command,
            "get_audit_ledger_integrity_status",
            return_value=audit_status,
        ),
        override_settings(MEDIA_ROOT=str(protected_media_root / "runtime")),
    ]


def _run_health_with_patches(
    tmp_path: Path,
    audit_status: Mapping[str, object],
) -> dict[str, Any]:
    output = StringIO()
    patches = _health_command_patches(tmp_path=tmp_path, audit_status=audit_status)
    with ExitStack() as stack:
        for active_patch in patches:
            stack.enter_context(active_patch)
        call_command("check_system_health", "--json", stdout=output)
    return cast(dict[str, Any], json.loads(output.getvalue()))


@pytest.mark.django_db
def test_check_system_health_includes_verified_audit_ledger_status(tmp_path: Path):
    audit_status = {
        "status": "verified",
        "verified": True,
        "checked_at": "2026-05-06T12:00:00+00:00",
        "entry_count": 3,
        "error": None,
        "source": "cache",
    }

    payload = _run_health_with_patches(tmp_path, audit_status)

    assert payload["checks"]["local_study_server_audit_ledger_integrity_verified"]
    assert payload["local_study_server"]["audit_ledger_integrity"] == audit_status


@pytest.mark.django_db
def test_check_system_health_rejects_local_profile_on_unverified_audit_ledger(
    tmp_path: Path,
):
    audit_status = {
        "status": "failed",
        "verified": False,
        "checked_at": "2026-05-06T12:00:00+00:00",
        "entry_count": 3,
        "error": "chain mismatch",
        "source": "cache",
    }
    output = StringIO()
    patches = _health_command_patches(tmp_path=tmp_path, audit_status=audit_status)

    with ExitStack() as stack:
        for active_patch in patches:
            stack.enter_context(active_patch)
        with pytest.raises(CommandError):
            call_command("check_system_health", "--json", stdout=output)

    payload = cast(dict[str, Any], json.loads(output.getvalue()))
    assert not payload["checks"]["local_study_server_audit_ledger_integrity_verified"]
    assert payload["local_study_server"]["audit_ledger_integrity"] == audit_status


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("stats", "failed_check"),
    [
        (
            {
                "failed_videos": 1,
                "failed_reports": 0,
                "stale_video_histories": 0,
                "stale_timeout_seconds": 7 * 60 * 60,
                "error": None,
            },
            "local_study_server_no_failed_anonymization",
        ),
        (
            {
                "failed_videos": 0,
                "failed_reports": 0,
                "stale_video_histories": 1,
                "stale_timeout_seconds": 7 * 60 * 60,
                "error": None,
            },
            "local_study_server_no_stale_video_processing",
        ),
    ],
)
def test_check_system_health_fails_closed_for_anonymization_processing(
    tmp_path: Path,
    stats: Mapping[str, object],
    failed_check: str,
):
    audit_status = {
        "status": "verified",
        "verified": True,
        "checked_at": "2026-05-06T12:00:00+00:00",
        "entry_count": 3,
        "error": None,
        "source": "cache",
    }
    output = StringIO()
    patches = _health_command_patches(tmp_path=tmp_path, audit_status=audit_status)

    with ExitStack() as stack:
        for active_patch in patches:
            stack.enter_context(active_patch)
        stack.enter_context(
            patch.object(
                health_command,
                "_anonymization_processing_stats",
                return_value=stats,
            )
        )
        with pytest.raises(CommandError, match=failed_check):
            call_command("check_system_health", "--json", stdout=output)

    payload = cast(dict[str, Any], json.loads(output.getvalue()))
    assert payload["checks"][failed_check] is False
    assert payload["local_study_server"]["anonymization_processing"] == stats

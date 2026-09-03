from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from endoreg_db.services.hub.cleanup import (
    UploadSourceCleanupBlocker,
    UploadSourceCleanupDecision,
    UploadSourceCleanupItem,
    UploadSourceMediaType,
    UploadSourceReaperResult,
)


def _result(*, applied: bool = False) -> UploadSourceReaperResult:
    return UploadSourceReaperResult(
        items=(
            UploadSourceCleanupItem(
                upload_job_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
                decision=(
                    UploadSourceCleanupDecision.COMPLETED
                    if applied
                    else UploadSourceCleanupDecision.DELETE
                ),
                blocker=UploadSourceCleanupBlocker.NONE,
                media_type=UploadSourceMediaType.REPORT,
                ingest_mode="api",
                age_seconds=int(timedelta(days=2).total_seconds()),
                reclaimable_bytes=4096,
                freed_bytes=4096 if applied else 0,
                applied=applied,
                receipt_id=(
                    uuid.UUID("22222222-2222-2222-2222-222222222222")
                    if applied
                    else None
                ),
            ),
        )
    )


def test_command_is_dry_run_by_default_and_emits_safe_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[dict[str, object]] = []

    def fake_reaper(**kwargs: object) -> UploadSourceReaperResult:
        calls.append(kwargs)
        return _result()

    monkeypatch.setattr(
        "endoreg_db.management.commands.reap_upload_job_sources.run_upload_job_source_reaper",
        fake_reaper,
    )

    call_command("reap_upload_job_sources", "--limit", "5", "--json")

    payload = json.loads(capsys.readouterr().out)
    assert calls == [{"apply": False, "upload_job_id": None, "limit": 5}]
    assert payload["mode"] == "dry_run"
    assert payload["reclaimable_bytes"] == 4096
    assert payload["items"][0]["blocker"] == "none"
    serialized = json.dumps(payload)
    assert "/home/" not in serialized
    assert "content_hash" not in serialized


@pytest.mark.parametrize(
    "args",
    [
        (),
        ("--limit", "0"),
        ("--limit", "-1"),
        (
            "--upload-job-id",
            "11111111-1111-1111-1111-111111111111",
            "--limit",
            "1",
        ),
        (
            "--upload-job-id",
            "11111111-1111-1111-1111-111111111111",
            "--repeat-until-empty",
        ),
    ],
)
def test_invalid_selectors_fail_loudly(args: tuple[str, ...]) -> None:
    with pytest.raises(CommandError):
        call_command("reap_upload_job_sources", *args)


def test_repeat_requires_apply() -> None:
    with pytest.raises(CommandError, match="requires --apply"):
        call_command(
            "reap_upload_job_sources",
            "--limit",
            "2",
            "--repeat-until-empty",
        )


def test_apply_is_independently_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "endoreg_db.management.commands.reap_upload_job_sources.upload_job_source_reaper_apply_enabled",
        lambda: False,
    )
    with pytest.raises(CommandError, match="apply is disabled"):
        call_command("reap_upload_job_sources", "--limit", "1", "--apply")


def test_apply_passes_explicit_intent_and_reports_freed_bytes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[dict[str, object]] = []

    def fake_reaper(**kwargs: object) -> UploadSourceReaperResult:
        calls.append(kwargs)
        return _result(applied=True)

    monkeypatch.setattr(
        "endoreg_db.management.commands.reap_upload_job_sources.run_upload_job_source_reaper",
        fake_reaper,
    )
    monkeypatch.setattr(
        "endoreg_db.management.commands.reap_upload_job_sources.upload_job_source_reaper_apply_enabled",
        lambda: True,
    )

    call_command(
        "reap_upload_job_sources",
        "--upload-job-id",
        "11111111-1111-1111-1111-111111111111",
        "--apply",
        "--json",
    )

    payload = json.loads(capsys.readouterr().out)
    assert calls == [
        {
            "apply": True,
            "upload_job_id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
            "limit": None,
        }
    ]
    assert payload["cleaned"] == 1
    assert payload["freed_bytes"] == 4096
    assert payload["items"][0]["receipt_id"] == ("22222222-2222-2222-2222-222222222222")

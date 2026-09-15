from __future__ import annotations

import json
import os
import time
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from django.contrib.auth.models import User
from django.core.management.base import CommandError
from django.core.management import call_command
from lx_dtypes.models.contracts.management_command import (
    ReapQuarantineCommandOptionsPayload,
)
from pydantic import ValidationError
from pytest import MonkeyPatch

from endoreg_db.config.env import DATA_DIR_ENV
from endoreg_db.management.commands import reap_quarantine as reap_command
from endoreg_db.models.hub.quarantine_item import QuarantineItem

pytestmark = pytest.mark.django_db


def _json_payload(output: StringIO) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(output.getvalue()))


def _reap_options(
    *,
    older_than_days: int = 30,
    dry_run: bool = True,
    confirm: bool = False,
    json_output: bool = True,
    approve_stale: bool = False,
    decision_reason: str = "",
    reviewed_by: str = "",
    delete_after_days: int = 0,
) -> dict[str, object]:
    payload = ReapQuarantineCommandOptionsPayload(
        older_than_days=older_than_days,
        dry_run=dry_run,
        confirm=confirm,
        json_output=json_output,
    ).model_dump(mode="python")
    payload["json"] = payload.pop("json_output")
    payload["approve_stale"] = approve_stale
    payload["decision_reason"] = decision_reason
    payload["reviewed_by"] = reviewed_by
    payload["delete_after_days"] = delete_after_days
    return payload


def test_reap_quarantine_defaults_to_dry_run(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    quarantine_dir = data_dir / "quarantine"
    quarantine_dir.mkdir(parents=True)
    stale_file = quarantine_dir / "stale.bin"
    stale_file.write_bytes(b"stale")
    old_timestamp = time.time() - (31 * 24 * 60 * 60)
    os.utime(stale_file, (old_timestamp, old_timestamp))
    monkeypatch.setenv(DATA_DIR_ENV, str(data_dir))

    output = StringIO()
    call_command(
        "reap_quarantine",
        stdout=output,
        **_reap_options(older_than_days=30, dry_run=True, confirm=False),
    )

    payload = _json_payload(output)
    assert payload["dry_run"] is True
    assert payload["pending_review_count"] == 1
    assert payload["candidate_count"] == 0
    assert payload["candidate_bytes"] == 0
    assert payload["deleted_count"] == 0
    assert stale_file.exists()
    assert QuarantineItem.objects.get().status == QuarantineItem.Status.PENDING_REVIEW


def test_reap_quarantine_confirm_does_not_delete_without_approval(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    quarantine_dir = data_dir / "quarantine"
    quarantine_dir.mkdir(parents=True)
    stale_file = quarantine_dir / "stale.bin"
    fresh_file = quarantine_dir / "fresh.bin"
    stale_file.write_bytes(b"stale")
    fresh_file.write_bytes(b"fresh")
    old_timestamp = time.time() - (31 * 24 * 60 * 60)
    os.utime(stale_file, (old_timestamp, old_timestamp))
    monkeypatch.setenv(DATA_DIR_ENV, str(data_dir))

    output = StringIO()
    call_command(
        "reap_quarantine",
        stdout=output,
        **_reap_options(older_than_days=30, dry_run=False, confirm=True),
    )

    payload = _json_payload(output)
    assert payload["dry_run"] is False
    assert payload["pending_review_count"] == 1
    assert payload["candidate_count"] == 0
    assert payload["deleted_count"] == 0
    assert stale_file.exists()
    assert fresh_file.exists()


def test_reap_quarantine_approve_stale_then_confirm_deletes_only_approved_files(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    quarantine_dir = data_dir / "quarantine"
    quarantine_dir.mkdir(parents=True)
    stale_file = quarantine_dir / "stale.bin"
    fresh_file = quarantine_dir / "fresh.bin"
    stale_file.write_bytes(b"stale")
    fresh_file.write_bytes(b"fresh")
    old_timestamp = time.time() - (31 * 24 * 60 * 60)
    os.utime(stale_file, (old_timestamp, old_timestamp))
    monkeypatch.setenv(DATA_DIR_ENV, str(data_dir))

    output = StringIO()
    call_command(
        "reap_quarantine",
        stdout=output,
        **_reap_options(
            older_than_days=30,
            dry_run=False,
            confirm=True,
            approve_stale=True,
            decision_reason="retention period elapsed",
        ),
    )

    payload = _json_payload(output)
    assert payload["dry_run"] is False
    assert payload["approved_count"] == 1
    assert payload["candidate_count"] == 1
    assert payload["candidate_bytes"] == len(b"stale")
    assert payload["deleted_count"] == 1
    assert not stale_file.exists()
    assert fresh_file.exists()


def test_reap_quarantine_preserves_service_order_and_arguments(
    monkeypatch: MonkeyPatch,
) -> None:
    events: list[tuple[str, object]] = []
    reviewer = User.objects.create_user(username="quarantine-reviewer")

    def fake_sync() -> reap_command.QuarantineSyncResult:
        events.append(("sync", None))
        return reap_command.QuarantineSyncResult(
            quarantine_dir=Path("/quarantine"),
            scanned_count=3,
            created_count=1,
            updated_count=2,
            missing_count=0,
            total_bytes=30,
        )

    def fake_approve(
        *,
        older_than_days: int,
        reason: str,
        reviewed_by: object,
        delete_after_days: int,
    ) -> reap_command.QuarantineApprovalResult:
        events.append(
            (
                "approve",
                (older_than_days, reason, reviewed_by, delete_after_days),
            )
        )
        return reap_command.QuarantineApprovalResult(
            approved_count=0,
            approved_items=(),
        )

    def fake_reap(
        *,
        older_than_days: int,
        dry_run: bool,
    ) -> reap_command.QuarantineReapResult:
        events.append(("reap", (older_than_days, dry_run)))
        return reap_command.QuarantineReapResult(
            quarantine_dir=Path("/quarantine"),
            dry_run=dry_run,
            candidate_count=0,
            candidate_bytes=0,
            deleted_count=0,
            candidates=(),
            deleted=(),
            missing_count=0,
        )

    def fake_pending(*, older_than_days: int) -> list[QuarantineItem]:
        events.append(("pending", older_than_days))
        return []

    monkeypatch.setattr(reap_command, "sync_quarantine_inventory", fake_sync)
    monkeypatch.setattr(reap_command, "approve_stale_quarantine_items", fake_approve)
    monkeypatch.setattr(reap_command, "reap_approved_quarantine_items", fake_reap)
    monkeypatch.setattr(reap_command, "stale_pending_review_items", fake_pending)

    output = StringIO()
    reap_command.Command(stdout=output).handle(
        **_reap_options(
            older_than_days=12,
            dry_run=False,
            confirm=True,
            approve_stale=True,
            decision_reason="  retention elapsed  ",
            reviewed_by="  quarantine-reviewer  ",
            delete_after_days=4,
        )
    )

    assert events == [
        ("sync", None),
        ("approve", (12, "retention elapsed", reviewer, 4)),
        ("reap", (12, False)),
        ("pending", 12),
    ]


def test_reap_quarantine_preserves_human_output(
    monkeypatch: MonkeyPatch,
) -> None:
    deleted_item = cast(Any, SimpleNamespace(path="/quarantine/deleted.bin"))
    pending_item = cast(Any, SimpleNamespace(path="/quarantine/pending.bin"))

    def fake_approve(
        *,
        older_than_days: int,
        reason: str,
        reviewed_by: object,
        delete_after_days: int,
    ) -> reap_command.QuarantineApprovalResult:
        _ = older_than_days, reason, reviewed_by, delete_after_days
        return reap_command.QuarantineApprovalResult(
            approved_count=2,
            approved_items=(),
        )

    def fake_reap(
        *,
        older_than_days: int,
        dry_run: bool,
    ) -> reap_command.QuarantineReapResult:
        _ = older_than_days, dry_run
        return reap_command.QuarantineReapResult(
            quarantine_dir=Path("/quarantine"),
            dry_run=False,
            candidate_count=1,
            candidate_bytes=10,
            deleted_count=1,
            candidates=(deleted_item,),
            deleted=(deleted_item,),
            missing_count=0,
        )

    def fake_pending(*, older_than_days: int) -> list[QuarantineItem]:
        _ = older_than_days
        return [pending_item]

    monkeypatch.setattr(
        reap_command,
        "sync_quarantine_inventory",
        lambda: reap_command.QuarantineSyncResult(
            quarantine_dir=Path("/quarantine"),
            scanned_count=2,
            created_count=2,
            updated_count=0,
            missing_count=0,
            total_bytes=20,
        ),
    )
    monkeypatch.setattr(
        reap_command,
        "approve_stale_quarantine_items",
        fake_approve,
    )
    monkeypatch.setattr(
        reap_command,
        "reap_approved_quarantine_items",
        fake_reap,
    )
    monkeypatch.setattr(
        reap_command,
        "stale_pending_review_items",
        fake_pending,
    )

    output = StringIO()
    reap_command.Command(stdout=output).handle(
        **_reap_options(
            dry_run=False,
            confirm=True,
            json_output=False,
            approve_stale=True,
            decision_reason="retention elapsed",
        )
    )

    assert output.getvalue() == (
        "confirmed: 1 approved quarantine files eligible for deletion; "
        "1 pending review\n"
        "approved 2 files\n"
        "deleted 1 files\n"
    )


def test_reap_quarantine_preserves_boundary_errors_and_sync_timing(
    monkeypatch: MonkeyPatch,
) -> None:
    sync_calls = 0

    def fake_sync() -> None:
        nonlocal sync_calls
        sync_calls += 1

    monkeypatch.setattr(reap_command, "sync_quarantine_inventory", fake_sync)

    with pytest.raises(
        CommandError,
        match="delete_after_days must not be negative",
    ):
        reap_command.Command().handle(**_reap_options(delete_after_days=-1))
    assert sync_calls == 0

    with pytest.raises(
        CommandError,
        match="--decision-reason is required with --approve-stale",
    ):
        reap_command.Command().handle(
            **_reap_options(approve_stale=True, decision_reason="")
        )
    assert sync_calls == 1


def test_reap_quarantine_preserves_validation_error_chaining() -> None:
    options = _reap_options()
    options["older_than_days"] = -1

    with pytest.raises(CommandError) as exc_info:
        reap_command.Command().handle(**options)

    assert isinstance(exc_info.value.__cause__, ValidationError)


def test_reap_quarantine_rejects_unknown_reviewer_before_sync(
    monkeypatch: MonkeyPatch,
) -> None:
    sync_called = False

    def fake_sync() -> None:
        nonlocal sync_called
        sync_called = True

    monkeypatch.setattr(reap_command, "sync_quarantine_inventory", fake_sync)

    with pytest.raises(
        CommandError,
        match="Unknown reviewer username: missing-reviewer",
    ):
        reap_command.Command().handle(**_reap_options(reviewed_by="missing-reviewer"))

    assert sync_called is False


def test_reap_quarantine_preserves_service_exception_identity(
    monkeypatch: MonkeyPatch,
) -> None:
    service_error = RuntimeError("inventory unavailable")

    def fail_sync() -> None:
        raise service_error

    monkeypatch.setattr(reap_command, "sync_quarantine_inventory", fail_sync)

    with pytest.raises(RuntimeError) as exc_info:
        reap_command.Command().handle(**_reap_options())

    assert exc_info.value is service_error

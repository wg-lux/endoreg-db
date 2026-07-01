from __future__ import annotations

import json
import os
import time
from io import StringIO
from pathlib import Path
from typing import Any, cast

import pytest
from pytest import MonkeyPatch
from django.core.management import call_command
from lx_dtypes.models.contracts.management_command import (
    ReapQuarantineCommandOptionsPayload,
)

from endoreg_db.config.env import DATA_DIR_ENV
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

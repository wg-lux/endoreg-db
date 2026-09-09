from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from endoreg_db.config.env import DATA_DIR_ENV
from endoreg_db.models.hub.quarantine_item import QuarantineItem
from endoreg_db.services.hub.quarantine import (
    approve_stale_quarantine_items,
    reap_approved_quarantine_items,
    retain_quarantine_item,
    sync_quarantine_inventory,
)


def _set_old_mtime(path: Path, *, days_old: int = 31) -> None:
    timestamp = time.time() - (days_old * 24 * 60 * 60)
    os.utime(path, (timestamp, timestamp))


@pytest.mark.django_db
def test_sync_quarantine_inventory_indexes_existing_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    quarantine_dir = data_dir / "quarantine"
    quarantine_dir.mkdir(parents=True)
    stale_file = quarantine_dir / "stale.bin"
    stale_file.write_bytes(b"stale")
    _set_old_mtime(stale_file)
    monkeypatch.setenv(DATA_DIR_ENV, str(data_dir))

    result = sync_quarantine_inventory()

    assert result.scanned_count == 1
    assert result.created_count == 1
    item = QuarantineItem.objects.get()
    assert item.status == QuarantineItem.Status.PENDING_REVIEW
    assert item.relative_path == "stale.bin"
    assert item.size_bytes == len(b"stale")


@pytest.mark.django_db
def test_approved_stale_quarantine_item_is_reaped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    quarantine_dir = data_dir / "quarantine"
    quarantine_dir.mkdir(parents=True)
    stale_file = quarantine_dir / "stale.bin"
    stale_file.write_bytes(b"stale")
    _set_old_mtime(stale_file)
    monkeypatch.setenv(DATA_DIR_ENV, str(data_dir))
    sync_quarantine_inventory()

    approval = approve_stale_quarantine_items(
        older_than_days=30,
        reason="retention period elapsed",
    )
    reap_result = reap_approved_quarantine_items(
        older_than_days=30,
        dry_run=False,
    )

    assert approval.approved_count == 1
    assert reap_result.deleted_count == 1
    assert not stale_file.exists()
    item = QuarantineItem.objects.get()
    assert item.status == QuarantineItem.Status.DELETED
    assert item.deleted_at is not None


@pytest.mark.django_db
def test_retained_quarantine_item_is_not_reaped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    quarantine_dir = data_dir / "quarantine"
    quarantine_dir.mkdir(parents=True)
    stale_file = quarantine_dir / "stale.bin"
    stale_file.write_bytes(b"stale")
    _set_old_mtime(stale_file)
    monkeypatch.setenv(DATA_DIR_ENV, str(data_dir))
    sync_quarantine_inventory()
    item = QuarantineItem.objects.get()
    retain_quarantine_item(item, reason="manual investigation required")

    reap_result = reap_approved_quarantine_items(
        older_than_days=30,
        dry_run=False,
    )

    assert reap_result.deleted_count == 0
    assert stale_file.exists()
    item.refresh_from_db()
    assert item.status == QuarantineItem.Status.RETAINED

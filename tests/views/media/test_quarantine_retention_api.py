from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Protocol, cast

import pytest
from django.contrib.auth.models import Group, User
from django.test import Client

from endoreg_db.config.env import DATA_DIR_ENV
from endoreg_db.helpers.typing import m2m_add_relation


class _ResponseLike(Protocol):
    status_code: int
    content: bytes

    def json(self) -> dict[str, object]: ...


def _set_old_mtime(path: Path, *, days_old: int = 31) -> None:
    timestamp = time.time() - (days_old * 24 * 60 * 60)
    os.utime(path, (timestamp, timestamp))


@pytest.mark.django_db
def test_quarantine_retention_api_requires_approval_before_reap(
    client: Client,
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
    user = User.objects.create_user(username="quarantine-reviewer")
    m2m_add_relation(user.groups).add(Group.objects.create(name="anonymization:write"))  # type: ignore[arg-type]
    client.force_login(user)

    sync_response = cast(
        _ResponseLike,
        client.post(
            "/api/media/quarantine/sync/",
            data={"older_than_days": 30},
            content_type="application/json",
        ),
    )
    assert sync_response.status_code == 200, sync_response.content
    sync_payload = sync_response.json()
    assert sync_payload["stale_pending_review_count"] == 1

    list_response = cast(_ResponseLike, client.get("/api/media/quarantine/"))
    assert list_response.status_code == 200, list_response.content
    list_payload = list_response.json()
    results = cast(list[dict[str, object]], list_payload["results"])
    item_id = str(results[0]["id"])
    assert results[0]["status"] == "pending_review"

    dry_reap_response = cast(
        _ResponseLike,
        client.post(
            "/api/media/quarantine/reap-approved/",
            data={"older_than_days": 30, "dry_run": False},
            content_type="application/json",
        ),
    )
    assert dry_reap_response.status_code == 200, dry_reap_response.content
    assert dry_reap_response.json()["deleted_count"] == 0
    assert stale_file.exists()

    approve_response = cast(
        _ResponseLike,
        client.post(
            f"/api/media/quarantine/{item_id}/approve-deletion/",
            data={"decision_reason": "retention period elapsed"},
            content_type="application/json",
        ),
    )
    assert approve_response.status_code == 200, approve_response.content
    assert approve_response.json()["status"] == "approved_for_deletion"

    reap_response = cast(
        _ResponseLike,
        client.post(
            "/api/media/quarantine/reap-approved/",
            data={"older_than_days": 30, "dry_run": False},
            content_type="application/json",
        ),
    )
    assert reap_response.status_code == 200, reap_response.content
    assert reap_response.json()["deleted_count"] == 1
    assert not stale_file.exists()

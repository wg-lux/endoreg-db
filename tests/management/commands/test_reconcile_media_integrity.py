from __future__ import annotations

import json
from io import StringIO
from typing import Any, cast

import pytest
from pytest import MonkeyPatch
from django.core.management import call_command
from lx_dtypes.models.contracts.management_command import (
    ReconcileMediaIntegrityCommandOptionsPayload,
)

from endoreg_db.services.media_integrity import MediaIntegritySummary


def _json_payload(output: StringIO) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(output.getvalue()))


def _media_integrity_options() -> dict[str, object]:
    return ReconcileMediaIntegrityCommandOptionsPayload(
        dry_run=True,
        json=True,
        video_id=[22, 34],
        check_frames=True,
        repair_frames=True,
        repair_frame=[0, 17],
        check_ffmpeg_meta=True,
        repair_ffmpeg_meta=True,
        check_streamable_probe=True,
        cleanup_stale_artifacts=True,
    ).model_dump(mode="python")



@pytest.mark.unit
def test_reconcile_media_integrity_command_passes_targeted_recovery_options(
    monkeypatch: MonkeyPatch,
) -> None:
    from endoreg_db.management.commands import (
        reconcile_media_integrity as command_module,
    )

    captured: dict[str, object] = {}

    def fake_reconcile_media_integrity(**kwargs: object) -> MediaIntegritySummary:
        captured.update(kwargs)
        return MediaIntegritySummary(
            checked_videos=2,
            checked_upload_jobs=0,
            repaired_records=0,
            lost_records=0,
        )

    monkeypatch.setattr(
        command_module,
        "reconcile_media_integrity",
        fake_reconcile_media_integrity,
        raising=True,
    )

    output = StringIO()
    call_command(
        "reconcile_media_integrity",
        stdout=output,
        **_media_integrity_options(),
    )

    assert captured == {
        "dry_run": True,
        "video_ids": [22, 34],
        "check_frames": True,
        "repair_frames": True,
        "repair_frame_numbers": [0, 17],
        "check_ffmpeg_meta": True,
        "repair_ffmpeg_meta": True,
        "check_streamable_probe": True,
        "cleanup_stale_artifacts": True,
    }
    payload = _json_payload(output)
    assert payload["checked_videos"] == 2
    assert payload["dry_run"] is True

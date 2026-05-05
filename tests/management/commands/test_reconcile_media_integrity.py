from __future__ import annotations

import json
from io import StringIO

import pytest
from django.core.management import call_command

from endoreg_db.services.media_integrity import MediaIntegritySummary


@pytest.mark.unit
def test_reconcile_media_integrity_command_passes_targeted_recovery_options(
    monkeypatch,
):
    from endoreg_db.management.commands import (
        reconcile_media_integrity as command_module,
    )

    captured: dict[str, object] = {}

    def fake_reconcile_media_integrity(**kwargs):
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
        "--dry-run",
        "--json",
        "--video-id",
        "22",
        "--video-id",
        "34",
        "--check-frames",
        "--repair-frames",
        "--repair-frame",
        "0",
        "--repair-frame",
        "17",
        "--check-ffmpeg-meta",
        "--repair-ffmpeg-meta",
        "--check-streamable-probe",
        "--cleanup-stale-artifacts",
        stdout=output,
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
    payload = json.loads(output.getvalue())
    assert payload["checked_videos"] == 2
    assert payload["dry_run"] is True

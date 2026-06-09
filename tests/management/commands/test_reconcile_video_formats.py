from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from endoreg_db.management.commands import reconcile_video_formats as command_module
from endoreg_db.services.video_format_reconciliation import VideoFormatSummary


@pytest.mark.unit
def test_reconcile_video_formats_command_allows_legacy_only_scan(monkeypatch):
    captured: dict[str, object] = {}

    def fake_reconcile_video_formats(**kwargs):
        captured.update(kwargs)
        return VideoFormatSummary(
            include_legacy_roots=bool(kwargs["include_legacy_roots"])
        )

    monkeypatch.setattr(
        command_module,
        "reconcile_video_formats",
        fake_reconcile_video_formats,
    )

    stdout = StringIO()
    call_command(
        "reconcile_video_formats",
        "--no-default-roots",
        "--include-legacy-roots",
        "--json",
        stdout=stdout,
    )

    assert captured["roots"] == []
    assert captured["include_default_roots"] is False
    assert captured["include_legacy_roots"] is True
    assert '"include_legacy_roots": true' in stdout.getvalue()

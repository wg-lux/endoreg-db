from __future__ import annotations

from io import StringIO

import pytest
from pytest import MonkeyPatch
from django.core.management.base import CommandError
from django.core.management import call_command

from endoreg_db.management.commands import reconcile_video_formats as command_module
from lx_dtypes.models.contracts.management_command import (
    ReconcileVideoFormatsCommandOptionsPayload,
)
from endoreg_db.services.video_format_reconciliation import VideoFormatSummary


def _command_options() -> dict[str, object]:
    payload = ReconcileVideoFormatsCommandOptionsPayload(
        root=[],
        include_default_roots=False,
        no_default_roots=True,
        include_legacy_roots=True,
        extension=[],
        dry_run=False,
        repair=False,
        in_place=False,
        allow_unmanaged_root=False,
        include_compliant=False,
        max_files=0,
        min_free_bytes=0,
        force_cpu=False,
        fail_on_non_compliant=False,
        json_output=True,
    ).model_dump(mode="python")
    payload["json"] = payload.pop("json_output")
    return payload


@pytest.mark.unit
def test_reconcile_video_formats_command_allows_legacy_only_scan(
    monkeypatch: MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_reconcile_video_formats(**kwargs: object) -> VideoFormatSummary:
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
        stdout=stdout,
        **_command_options(),
    )

    assert captured["roots"] == []
    assert captured["include_default_roots"] is False
    assert captured["include_legacy_roots"] is True
    assert '"include_legacy_roots": true' in stdout.getvalue()


@pytest.mark.unit
def test_reconcile_video_formats_command_requires_safe_repair_mode() -> None:
    options = _command_options()
    options["repair"] = True

    with pytest.raises(CommandError, match="--in-place or --dry-run"):
        call_command("reconcile_video_formats", **options)


@pytest.mark.unit
def test_reconcile_video_formats_command_requires_a_scan_root() -> None:
    options = _command_options()
    options["include_legacy_roots"] = False

    with pytest.raises(CommandError, match="No scan roots selected"):
        call_command("reconcile_video_formats", **options)


@pytest.mark.unit
def test_reconcile_video_formats_command_fails_for_unresolved_issues(
    monkeypatch: MonkeyPatch,
) -> None:
    def fake_reconcile_video_formats(**_kwargs: object) -> VideoFormatSummary:
        return VideoFormatSummary(
            non_compliant_files=2,
            invalid_files=1,
            repaired_files=1,
        )

    monkeypatch.setattr(
        command_module,
        "reconcile_video_formats",
        fake_reconcile_video_formats,
    )
    options = _command_options()
    options["fail_on_non_compliant"] = True

    with pytest.raises(CommandError, match="found 2 issues"):
        call_command("reconcile_video_formats", **options)

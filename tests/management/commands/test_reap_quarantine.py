from __future__ import annotations

import json
import os
import time
from io import StringIO

from django.core.management import call_command


def test_reap_quarantine_defaults_to_dry_run(tmp_path, monkeypatch):
    quarantine_dir = tmp_path / "quarantine"
    quarantine_dir.mkdir()
    stale_file = quarantine_dir / "stale.bin"
    stale_file.write_bytes(b"stale")
    old_timestamp = time.time() - (31 * 24 * 60 * 60)
    os.utime(stale_file, (old_timestamp, old_timestamp))
    monkeypatch.setattr(
        "endoreg_db.management.commands.reap_quarantine.QUARANTINE_DIR",
        quarantine_dir,
    )

    output = StringIO()
    call_command("reap_quarantine", "--older-than-days", "30", "--json", stdout=output)

    payload = json.loads(output.getvalue())
    assert payload["dry_run"] is True
    assert payload["candidate_count"] == 1
    assert payload["candidate_bytes"] == len(b"stale")
    assert payload["deleted_count"] == 0
    assert stale_file.exists()


def test_reap_quarantine_confirm_deletes_only_stale_files(tmp_path, monkeypatch):
    quarantine_dir = tmp_path / "quarantine"
    quarantine_dir.mkdir()
    stale_file = quarantine_dir / "stale.bin"
    fresh_file = quarantine_dir / "fresh.bin"
    stale_file.write_bytes(b"stale")
    fresh_file.write_bytes(b"fresh")
    old_timestamp = time.time() - (31 * 24 * 60 * 60)
    os.utime(stale_file, (old_timestamp, old_timestamp))
    monkeypatch.setattr(
        "endoreg_db.management.commands.reap_quarantine.QUARANTINE_DIR",
        quarantine_dir,
    )

    output = StringIO()
    call_command(
        "reap_quarantine",
        "--older-than-days",
        "30",
        "--confirm",
        "--json",
        stdout=output,
    )

    payload = json.loads(output.getvalue())
    assert payload["dry_run"] is False
    assert payload["candidate_count"] == 1
    assert payload["candidate_bytes"] == len(b"stale")
    assert payload["deleted_count"] == 1
    assert not stale_file.exists()
    assert fresh_file.exists()

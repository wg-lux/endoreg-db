from __future__ import annotations

import csv
import json
from io import StringIO
from pathlib import Path

import pytest
import yaml
from django.core.management import call_command
from django.core.management.base import CommandError

from endoreg_db.management.commands.build_k_pseudonymous_release import (
    _encode_csv,  # pyright: ignore[reportPrivateUsage]
)


def _write_config(path: Path, *, tau_max: float = 1.0) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0",
                "release_columns": ["center", "age_band", "diagnosis"],
                "quasi_identifiers": ["center", "age_band"],
                "sensitive_attributes": [
                    {
                        "name": "diagnosis",
                        "allowed_values": ["x", "y"],
                        "l_diversity": 2,
                        "t_closeness": 0.2,
                    }
                ],
                "utility_features": [
                    {
                        "name": "diagnosis",
                        "kind": "categorical",
                        "weight": 1.0,
                    }
                ],
                "k": 2,
                "tau_max": tau_max,
                "max_synthetic_rows": 2,
                "synthetic_rows_count_toward_k": True,
                "recipient_can_observe_synthetic_provenance": False,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _run_release(
    tmp_path: Path,
    *,
    center: str = "a",
    tau_max: float = 1.0,
    stdout: StringIO | None = None,
) -> None:
    config_path = tmp_path / "policy.yaml"
    input_path = tmp_path / "input.csv"
    _write_config(config_path, tau_max=tau_max)
    input_path.write_text(
        f"center,age_band,diagnosis\n{center},50-59,x\nb,60-69,x\nb,60-69,y\n",
        encoding="utf-8",
    )
    call_command(
        "build_k_pseudonymous_release",
        str(config_path),
        str(input_path),
        "--release-output",
        str(tmp_path / "release.csv"),
        "--audit-output",
        str(tmp_path / "protected" / "audit.json"),
        stdout=stdout,
    )


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@"])
def test_command_writes_escaped_release_and_protected_audit(
    tmp_path: Path,
    prefix: str,
) -> None:
    release_path = tmp_path / "release.csv"
    audit_path = tmp_path / "protected" / "audit.json"
    stdout = StringIO()
    _run_release(tmp_path, center=f"{prefix}test", stdout=stdout)
    assert f"\r\n'{prefix}test,50-59,x\r\n".encode("utf-8") in release_path.read_bytes()
    with release_path.open("r", encoding="utf-8", newline="") as handle:
        released_rows = list(csv.DictReader(handle))
    manifest = json.loads(audit_path.read_text(encoding="utf-8"))
    assert len(released_rows) == 4
    assert set(released_rows[0]) == {"center", "age_band", "diagnosis"}
    assert manifest["status"] == "released"
    assert manifest["synthetic_row_indices"] == [3]
    assert len(manifest["source_table_sha256"]) == 64
    assert len(manifest["release_table_sha256"]) == 64
    assert audit_path.stat().st_mode & 0o777 == 0o600
    assert release_path.stat().st_mode & 0o777 == 0o600
    assert "Release predicate satisfied" in stdout.getvalue()


def test_command_removes_stale_release_when_predicate_is_not_satisfied(
    tmp_path: Path,
) -> None:
    release_path = tmp_path / "release.csv"
    release_path.write_text("stale unsafe release", encoding="utf-8")
    with pytest.raises(CommandError, match="no release CSV was retained"):
        _run_release(tmp_path, tau_max=0.0)
    assert not release_path.exists()
    manifest = json.loads(
        (tmp_path / "protected" / "audit.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "no_release"


def test_release_csv_preserves_numbers() -> None:
    assert _encode_csv(({"value": -7},), fieldnames=("value",)) == b"value\r\n-7\r\n"

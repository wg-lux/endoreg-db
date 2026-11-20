"""Contract tests for the reusable frame annotation export script."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from lx_dtypes.models.contracts import export_config, export_result

from endoreg_db.export.frames.export_frames_with_labels import (
    AnnotationExporterClient,
    ExportJobFailedError,
)
from scripts import export_frame_annotations as script

EXAMPLE_CONFIG = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "examples"
    / "frame_annotation_export.yml"
)


@pytest.mark.no_db
@pytest.mark.parametrize(
    ("output_dir", "expected_output_dir"),
    [
        (None, "data/export/frame_annotations"),
        (Path("data/export/run_002"), Path("data/export/run_002")),
    ],
)
def test_example_config_validates_with_optional_output_dir(
    output_dir: Path | None, expected_output_dir: str | Path
) -> None:
    config = script.load_config(EXAMPLE_CONFIG, output_dir=output_dir)

    assert config.output_dir == expected_output_dir
    assert config.export_profile == "pts_dataset_v1"
    assert config.only_validated is True


@pytest.mark.no_db
def test_validate_only_prints_config_without_exporting(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def _unexpected_export(config: export_config) -> export_result:
        raise AssertionError("validate-only must not export")

    monkeypatch.setattr(script, "run_export", _unexpected_export)

    exit_code = script.main(["--config", str(EXAMPLE_CONFIG), "--validate-only"])

    assert exit_code == 0
    assert "format: csv (pts_dataset_v1)" in capsys.readouterr().out


@pytest.mark.no_db
@pytest.mark.parametrize(
    "config_text",
    [
        "output_path: annotations.csv\nunknown_key: true\n",
        "output_dir: data/export\n",
        "output_path: annotations.csv\ntranscode_fps: 0\n",
    ],
)
def test_invalid_config_fails_before_export(
    tmp_path: Path, config_text: str, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "export.yml"
    config_path.write_text(config_text)

    exit_code = script.main(["--config", str(config_path)])

    assert exit_code == 2
    assert "Invalid export config" in capsys.readouterr().err


@pytest.mark.no_db
def test_missing_config_fails_before_export(tmp_path: Path) -> None:
    assert script.main(["--config", str(tmp_path / "missing.yml")]) == 2


@pytest.mark.no_db
def test_export_requires_django_settings_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DJANGO_SETTINGS_MODULE")

    with pytest.raises(SystemExit, match="DJANGO_SETTINGS_MODULE"):
        script.main(["--config", str(EXAMPLE_CONFIG)])


@pytest.mark.no_db
def test_export_failure_returns_nonzero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _failing_export(
        self: AnnotationExporterClient, config: export_config
    ) -> export_result:
        raise ExportJobFailedError("annotation export failed")

    monkeypatch.setattr(AnnotationExporterClient, "run_export", _failing_export)

    exit_code = script.main(["--config", str(EXAMPLE_CONFIG)])

    assert exit_code == 1
    assert "Export failed: annotation export failed" in capsys.readouterr().err


@pytest.mark.django_db
def test_script_runs_real_export_to_output_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "export.yml"
    config_path.write_text(
        "output_path: annotations.csv\n"
        "export_profile: legacy_table_v1\n"
        "export_frames: false\n"
        "all_centers: true\n"
    )
    output_dir = tmp_path / "run_001"

    exit_code = script.main(
        ["--config", str(config_path), "--output-dir", str(output_dir)]
    )

    table = output_dir / "annotations.csv"
    assert exit_code == 0
    assert table.is_file()
    with table.open(newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0], "export table must start with a header row"
    assert rows[1:] == []
    assert f"table: {table}" in capsys.readouterr().out

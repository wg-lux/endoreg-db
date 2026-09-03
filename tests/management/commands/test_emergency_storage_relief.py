from __future__ import annotations

import json
from pathlib import Path

import pytest

from endoreg_db.management.commands import emergency_storage_relief as relief


def _config(tmp_path: Path, export_dir: Path) -> relief.StorageReliefConfig:
    archive_root = tmp_path / "archive"
    return relief.StorageReliefConfig(
        archive_root=archive_root,
        manifest_dir=tmp_path / "manifests",
        staging_root=archive_root / "staging",
        dry_run=True,
        delete_after_verify=True,
        include_legacy_processed_duplicates=False,
        include_validated_export_bundles=True,
        validated_export_dirs=[export_dir],
        validated_export_marker_names=["validated.json"],
        legacy_duplicate_sources=[],
    )


def _write_bundle(export_dir: Path) -> tuple[Path, Path]:
    bundle_root = export_dir / "case-1"
    bundle_root.mkdir(parents=True)
    marker = bundle_root / "validated.json"
    marker.write_text(
        json.dumps(
            {
                "validated": True,
                "resources": [{"kind": "video", "id": 7}],
            }
        ),
        encoding="utf-8",
    )
    (bundle_root / "payload.bin").write_bytes(b"validated export")
    return bundle_root, marker


def test_archive_validated_export_bundles_plans_all_bundle_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export_dir = tmp_path / "exports"
    bundle_root, marker = _write_bundle(export_dir)

    def resource_is_validated(_resource: dict[str, object]) -> bool:
        return True

    monkeypatch.setattr(relief, "resource_is_validated", resource_is_validated)

    items = relief.archive_validated_export_bundles(
        config=_config(tmp_path, export_dir)
    )

    assert len(items) == 2
    assert {item["status"] for item in items} == {"planned"}
    assert {Path(item["source"]).name for item in items} == {
        "payload.bin",
        "validated.json",
    }
    assert all(item["category"] == "validated_export_bundle" for item in items)
    assert all(item["bundle_root"] == str(bundle_root) for item in items)
    assert all(item["marker"] == str(marker) for item in items)
    assert marker.exists()


def test_archive_validated_export_bundles_skips_unvalidated_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    export_dir = tmp_path / "exports"
    bundle_root, marker = _write_bundle(export_dir)

    def resource_is_validated(_resource: dict[str, object]) -> bool:
        return False

    monkeypatch.setattr(relief, "resource_is_validated", resource_is_validated)

    items = relief.archive_validated_export_bundles(
        config=_config(tmp_path, export_dir)
    )

    assert items == []
    assert marker.exists()
    assert (bundle_root / "payload.bin").exists()
    event = json.loads(capsys.readouterr().out)
    assert event["event"] == "lx_annotate_storage_relief_skip"
    assert event["reason"] == "export bundle resources are not validated"

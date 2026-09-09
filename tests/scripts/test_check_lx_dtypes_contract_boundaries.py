from __future__ import annotations

from pathlib import Path

from scripts.check_lx_dtypes_contract_boundaries import (
    ContractBoundaryPolicy,
    check_contract_boundaries,
    discover_local_type_aliases,
    discover_lx_dtypes_realiases,
    exact_dependency_pin,
)


def _policy(**updates: object) -> ContractBoundaryPolicy:
    values: dict[str, object] = {
        "dependency_name": "lx-dtypes",
        "source_root": "endoreg_db",
        "maximum_local_type_aliases": 2,
        "forbid_lx_dtypes_realiases": True,
        "require_exact_dependency_pin": True,
        "require_installed_version_match": True,
        "canonical_import_prefixes": [
            "lx_dtypes.models.contracts",
            "lx_dtypes.models.interface",
            "lx_dtypes.models.knowledge_base",
        ],
    }
    values.update(updates)
    return ContractBoundaryPolicy.model_validate(values)


def test_discovers_legacy_and_pep_695_type_aliases(tmp_path: Path) -> None:
    source_root = tmp_path / "endoreg_db"
    source_root.mkdir()
    (source_root / "aliases.py").write_text(
        "from typing import TypeAlias\n"
        "Legacy: TypeAlias = str | None\n"
        "type Modern = tuple[str, ...]\n",
        encoding="utf-8",
    )

    aliases = discover_local_type_aliases(source_root)

    assert [(item.name, item.target) for item in aliases] == [
        ("Legacy", "str | None"),
        ("Modern", "tuple[str, ...]"),
    ]


def test_detects_direct_lx_dtypes_type_realiases(tmp_path: Path) -> None:
    source_root = tmp_path / "endoreg_db"
    source_root.mkdir()
    (source_root / "aliases.py").write_text(
        "from typing import TypeAlias\n"
        "from lx_dtypes.models.contracts.json_types import JsonValue\n"
        "import lx_dtypes.models.contracts.video_file as video_contracts\n"
        "LegacyJson: TypeAlias = JsonValue\n"
        "type VideoKind = video_contracts.VideoArtifactKind\n",
        encoding="utf-8",
    )

    aliases = discover_lx_dtypes_realiases(source_root)

    assert [item.local_name for item in aliases] == ["LegacyJson", "VideoKind"]
    assert aliases[0].canonical_target.endswith("json_types.JsonValue")
    assert aliases[1].canonical_target.endswith("video_file.VideoArtifactKind")


def test_contract_check_rejects_alias_growth_realias_and_version_drift(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "endoreg_db"
    source_root.mkdir()
    (source_root / "aliases.py").write_text(
        "from lx_dtypes.models.contracts.json_types import JsonValue\n"
        "type Local = str\n"
        "type Renamed = JsonValue\n",
        encoding="utf-8",
    )
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\ndependencies = ["lx-dtypes==1.2.3"]\n', encoding="utf-8"
    )

    report = check_contract_boundaries(
        _policy(maximum_local_type_aliases=1),
        project_root=tmp_path,
        pyproject_path=pyproject,
        installed_version="1.2.4",
    )

    assert report.errors == (
        "local type alias budget exceeded: 2 > 1",
        next(error for error in report.errors if "Renamed re-aliases" in error),
        "installed lx-dtypes version does not match the project contract pin: "
        "1.2.4 != 1.2.3",
    )


def test_exact_dependency_pin_rejects_ranges(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\ndependencies = ["lx-dtypes>=1.2.3"]\n', encoding="utf-8"
    )

    assert exact_dependency_pin(pyproject) is None

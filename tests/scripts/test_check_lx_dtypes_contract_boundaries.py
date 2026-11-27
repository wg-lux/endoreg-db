from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_lx_dtypes_contract_boundaries import (
    ContractBoundaryPolicy,
    check_contract_boundaries,
    discover_local_type_aliases,
    discover_lx_dtypes_realiases,
)


def _policy(**updates: object) -> ContractBoundaryPolicy:
    values: dict[str, object] = {
        "source_root": "endoreg_db",
        "maximum_local_type_aliases": 2,
        "forbid_lx_dtypes_realiases": True,
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


@pytest.mark.parametrize("suffix", [".py", ".pyi"])
@pytest.mark.parametrize(
    "declaration",
    [
        "type Local = str",
        "Local: TypeAlias = bool",
        "type Local = None | str",
        'Local: TypeAlias = "str | None"',
    ],
)
def test_rejects_trivial_aliases_without_new_helper_types(
    tmp_path: Path,
    suffix: str,
    declaration: str,
) -> None:
    source = tmp_path / "endoreg_db"
    source.mkdir()
    path = source / f"types{suffix}"
    path.write_text(declaration)
    policy = _policy(inline_alias_shapes=["str", "bool", "str | None"])

    report = check_contract_boundaries(policy, project_root=tmp_path)

    assert any("Local is a trivial alias" in error for error in report.errors)
    path.write_text("def read(value: str | None) -> bool: ...\n")
    assert check_contract_boundaries(policy, project_root=tmp_path).is_clean


@pytest.mark.parametrize(
    ("declaration", "expected"),
    [
        ("RenamedNull: TypeAlias = None", "use None directly"),
        ("type RenamedNull = None", "use None directly"),
        ("Binary: TypeAlias = File[bytes]", "import DjangoFile"),
        (
            "class Options(TypedDict):\n    verbose: bool",
            "VerboseManagementCommandOptionsPayload",
        ),
        (
            "class Saver(Protocol):\n    def save(self, name: str, content: DjangoFile, save: bool = True) -> None: ...",
            "import BinaryFieldFileSaver",
        ),
    ],
)
def test_rejects_reintroduced_boundary_shapes(
    tmp_path: Path,
    declaration: str,
    expected: str,
) -> None:
    source_root = tmp_path / "endoreg_db"
    source_root.mkdir()
    (source_root / "duplicate.py").write_text(declaration, encoding="utf-8")

    report = check_contract_boundaries(
        _policy(),
        project_root=tmp_path,
    )

    assert any(expected in error for error in report.errors)


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


def test_contract_check_rejects_alias_growth_and_realias(
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

    report = check_contract_boundaries(
        _policy(maximum_local_type_aliases=1),
        project_root=tmp_path,
    )

    assert report.errors == (
        "local type alias budget exceeded: 2 > 1",
        next(error for error in report.errors if "Renamed re-aliases" in error),
    )


@pytest.mark.parametrize("suffix", [".py", ".pyi"])
def test_canonical_type_owner_applies_to_implementations_and_stubs(
    tmp_path: Path,
    suffix: str,
) -> None:
    source_root = tmp_path / "endoreg_db"
    source_root.mkdir()
    declaration = "class YamlEntry(TypedDict):\n    fields: dict[str, str]\n"
    source = source_root / f"duplicate{suffix}"
    source.write_text(declaration, encoding="utf-8")
    policy = _policy(canonical_local_types={"YamlEntry": "schemas/model_data.py"})

    report = check_contract_boundaries(
        policy,
        project_root=tmp_path,
    )
    assert any(
        "YamlEntry belongs to schemas/model_data.py" in error for error in report.errors
    )

    source.unlink()
    canonical = source_root / "schemas" / "model_data.py"
    canonical.parent.mkdir()
    canonical.write_text(declaration, encoding="utf-8")
    report = check_contract_boundaries(
        policy,
        project_root=tmp_path,
    )
    assert report.is_clean


def test_verifies_terminology_versions_from_standard_package(tmp_path: Path) -> None:
    from lx_dtypes.knowledge_bases import list_packaged_knowledge_bases

    report = check_contract_boundaries(_policy(), project_root=tmp_path)

    assert report.is_clean
    assert report.terminologies
    assert report.terminologies == list_packaged_knowledge_bases()


@pytest.mark.parametrize("failure", ["missing_catalog", "missing_resources"])
def test_rejects_unavailable_packaged_terminology(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    from lx_dtypes.knowledge_bases import list_packaged_knowledge_bases

    from scripts import check_lx_dtypes_contract_boundaries as guard

    supplied = list_packaged_knowledge_bases()
    assert supplied
    unavailable = supplied[0].model_copy(
        update={"resource_root": "missing-terminology"}
    )
    monkeypatch.setattr(
        guard,
        "list_packaged_knowledge_bases",
        lambda: () if failure == "missing_catalog" else (unavailable,),
    )

    report = check_contract_boundaries(_policy(), project_root=tmp_path)

    assert not report.is_clean
    assert any("terminology" in error for error in report.errors)


@pytest.mark.parametrize("suffix", [".py", ".pyi"])
@pytest.mark.parametrize(
    "declaration",
    [
        'type Renamed = Literal["auto", "cache", "stream"]',
        'Renamed: TypeAlias = Literal["cache", "stream", "auto"]',
        'Renamed = typing.Literal["stream", "auto", "cache"]',
        'Renamed: TypeAlias = \'Literal["cache", "auto", "stream"]\'',
    ],
)
def test_rejects_renamed_shared_alias_shapes(
    tmp_path: Path,
    suffix: str,
    declaration: str,
) -> None:
    source = tmp_path / "endoreg_db"
    source.mkdir()
    (source / f"duplicate{suffix}").write_text(declaration)
    policy = _policy(
        canonical_alias_shapes={
            'Literal["cache", "stream", "auto"]': "lx_dtypes.models.contracts.video_file.FrameSourceMode",
        }
    )
    report = check_contract_boundaries(policy, project_root=tmp_path)
    assert any("Renamed duplicates" in error for error in report.errors)


def test_alias_shape_allows_only_named_canonical_owner(tmp_path: Path) -> None:
    source = tmp_path / "endoreg_db"
    source.mkdir()
    path = source / "types.py"
    policy = _policy(
        canonical_alias_shapes={
            "str | None": "endoreg_db.types.OptionalText",
        }
    )
    path.write_text("type OptionalText = None | str\n")
    assert check_contract_boundaries(policy, project_root=tmp_path).is_clean
    path.write_text("type DifferentName = str | None\n")
    assert not check_contract_boundaries(policy, project_root=tmp_path).is_clean
    path.write_text("type OptionalText = str | None\n")
    path.rename(source / "duplicate.py")
    assert not check_contract_boundaries(policy, project_root=tmp_path).is_clean


@pytest.mark.parametrize(
    "declaration",
    [
        "type FrameSourceMode = str",
        "FrameSourceMode = str",
        "class FrameSourceMode(TypedDict):\n    mode: str",
    ],
)
def test_shared_type_cannot_be_locally_redefined(
    tmp_path: Path,
    declaration: str,
) -> None:
    source = tmp_path / "endoreg_db"
    source.mkdir()
    (source / "duplicate.py").write_text(declaration)
    policy = _policy(
        canonical_type_imports={
            "FrameSourceMode": "lx_dtypes.models.contracts.video_file",
        }
    )
    report = check_contract_boundaries(policy, project_root=tmp_path)
    assert any("import FrameSourceMode from" in error for error in report.errors)


@pytest.mark.parametrize(
    "declaration",
    [
        "type Renamed = str | bool | float | int | None | list[Renamed] | dict[str, Renamed]",
        'Renamed: TypeAlias = str | int | float | bool | None | list["Renamed"] | dict[str, "Renamed"]',
    ],
)
def test_recursive_json_alias_cannot_hide_behind_a_new_name(
    tmp_path: Path,
    declaration: str,
) -> None:
    source = tmp_path / "endoreg_db"
    source.mkdir()
    (source / "duplicate.py").write_text(declaration)
    policy = _policy(
        canonical_alias_shapes={
            "str | int | float | bool | None | list[Self] | dict[str, Self]": "lx_dtypes.models.contracts.json_types.JsonValue",
        }
    )
    report = check_contract_boundaries(policy, project_root=tmp_path)
    assert any("Renamed duplicates" in error for error in report.errors)
    (source / "duplicate.py").write_text(
        "from lx_dtypes.models.contracts.json_types import JsonValue\n"
    )
    assert check_contract_boundaries(policy, project_root=tmp_path).is_clean

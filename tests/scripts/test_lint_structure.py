"""Invalid context must fail before agents navigate beyond a declared boundary."""

from pathlib import Path

import pytest
import yaml

from scripts.lint_structure import Structure, validate_manifests

pytestmark = pytest.mark.no_db


def _write(path: Path, document: Structure) -> None:
    path.write_text(yaml.safe_dump(document.model_dump(exclude_none=False)))


def _graph(root: Path) -> tuple[Path, Path]:
    child = root / "domain"
    child.mkdir()
    (child / "entry.py").touch()
    (root / "contract.md").touch()
    (root / "tests").mkdir()
    top = Structure.model_validate(
        {
            "version": 1,
            "tier": "root",
            "path": ".",
            "parent": None,
            "summary": "Root",
            "responsibilities": ["Route"],
            "boundaries": ["No clinical I/O"],
            "submodules": [
                {
                    "name": "domain",
                    "path": "domain",
                    "manifest": "domain/structure.yml",
                    "summary": "Domain",
                }
            ],
        }
    )
    local = Structure.model_validate(
        {
            "version": 1,
            "tier": "domain",
            "path": "domain",
            "parent": "../structure.yml",
            "summary": "Domain",
            "responsibilities": ["Own entry"],
            "boundaries": ["No transport"],
            "entrypoints": [
                {"name": "entry", "file": "entry.py", "purpose": "Execute"}
            ],
            "files": [{"file": "entry.py", "role": "Implementation"}],
            "dependencies": {"internal": ["domain/entry.py"], "external": ["python"]},
            "contracts": [{"name": "Contract", "doc": "contract.md"}],
            "tests": {"directory": "tests", "fast_lane_command": "pytest tests"},
        }
    )
    paths = root / "structure.yml", child / "structure.yml"
    _write(paths[0], top)
    _write(paths[1], local)
    return paths


def test_valid_graph_and_legacy_metadata(tmp_path: Path) -> None:
    paths = _graph(tmp_path)
    with paths[1].open("a") as stream:
        stream.write("module_moves: {}\nmodules: {}\n")
    assert validate_manifests(tmp_path, list(paths)) == []


@pytest.mark.parametrize(
    "contents", ["", "[]", "summary: only", "version: [", "version: 2", "null"]
)
def test_invalid_schema_is_reported(tmp_path: Path, contents: str) -> None:
    path = tmp_path / "structure.yml"
    path.write_text(contents)
    assert validate_manifests(tmp_path, [path])


@pytest.mark.parametrize(
    "missing", ["domain/entry.py", "contract.md", "tests", "domain/structure.yml"]
)
def test_stale_references_are_reported(tmp_path: Path, missing: str) -> None:
    paths = _graph(tmp_path)
    target = tmp_path / missing
    if target.is_dir():
        target.rmdir()
    else:
        target.unlink()
    assert validate_manifests(tmp_path, list(paths))


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("path", ".", "containing directory"),
        ("parent", "structure.yml", "not reciprocal"),
        ("parent", None, "non-root requires"),
        ("tier", "root", "non-root requires"),
        (
            "files",
            [{"file": "../contract.md", "role": "Escape"}],
            "outside local scope",
        ),
        ("files", [{"file": "/etc/passwd", "role": "Absolute"}], "relative path"),
        ("files", [{"file": "../../escape", "role": "Escape"}], "escapes repository"),
        ("dependencies", {"internal": ["nonexistent"]}, "Missing internal dependency"),
        (
            "tests",
            {
                "directory": "tests",
                "fast_lane_command": "pytest",
                "files": ["tests/missing.py"],
            },
            "Missing file",
        ),
    ],
)
def test_invalid_contracts(
    tmp_path: Path, field: str, value: object, expected: str
) -> None:
    paths = _graph(tmp_path)
    document = Structure.model_validate(
        yaml.safe_load(paths[1].read_text())
    ).model_dump()
    document[field] = value
    paths[1].write_text(yaml.safe_dump(document))
    assert any(expected in error for error in validate_manifests(tmp_path, list(paths)))


def test_orphan_and_duplicate_links(tmp_path: Path) -> None:
    paths = _graph(tmp_path)
    root = Structure.model_validate(yaml.safe_load(paths[0].read_text()))
    root.submodules.append(root.submodules[0])
    _write(paths[0], root)
    errors = validate_manifests(tmp_path, list(paths))
    assert any("duplicate submodule" in error for error in errors)
    assert any("duplicate parent" in error for error in errors)
    root.submodules.clear()
    _write(paths[0], root)
    assert any("Orphan" in error for error in validate_manifests(tmp_path, list(paths)))


def test_cycle_cannot_be_a_child_scope(tmp_path: Path) -> None:
    paths = _graph(tmp_path)
    child = Structure.model_validate(yaml.safe_load(paths[1].read_text()))
    child.submodules = [
        root_link
        for root_link in Structure.model_validate(
            yaml.safe_load(paths[0].read_text())
        ).submodules
    ]
    child.submodules[0].path = ".."
    child.submodules[0].manifest = "../structure.yml"
    _write(paths[1], child)
    assert any(
        "strict descendant" in error
        for error in validate_manifests(tmp_path, list(paths))
    )


def test_symlink_escape(tmp_path: Path) -> None:
    paths = _graph(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    entry = tmp_path / "domain/entry.py"
    entry.unlink()
    entry.symlink_to(outside)
    assert any(
        "escapes repository" in error
        for error in validate_manifests(tmp_path, list(paths))
    )


def test_repository_graph() -> None:
    root = Path(__file__).resolve().parents[2]
    # Test the real linked graph without recursive access to clinical storage.
    pending = [root / "structure.yml"]
    paths: list[Path] = []
    while pending:
        path = pending.pop()
        if path in paths:
            continue
        paths.append(path)
        document = Structure.model_validate(yaml.safe_load(path.read_text()))
        pending.extend(
            (path.parent / child.manifest).resolve() for child in document.submodules
        )
    assert validate_manifests(root, paths) == []

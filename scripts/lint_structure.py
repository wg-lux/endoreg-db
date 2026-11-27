"""Validate the repository's bounded, linked agent context manifests (read only)."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
import yaml


class Entry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1)
    file: str = Field(min_length=1)
    purpose: str = Field(min_length=1)


class FileEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    file: str = Field(min_length=1)
    role: str = Field(min_length=1)


class Submodule(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1)
    path: str = Field(min_length=1)
    manifest: str = Field(min_length=1)
    summary: str = Field(min_length=1)


class Dependencies(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    internal: list[str] = Field(default_factory=list)
    external: list[str] = Field(default_factory=list)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1)
    doc: str = Field(min_length=1)


class Tests(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    directory: str = Field(min_length=1)
    fast_lane_command: str = Field(min_length=1)
    files: list[str] = Field(default_factory=list)


class Structure(BaseModel):
    # Existing service relocation and hub ownership maps remain compatibility data.
    model_config = ConfigDict(extra="allow", strict=True)
    version: Literal[1]
    tier: Literal["root", "domain", "service", "module"]
    path: str = Field(min_length=1)
    parent: str | None
    summary: str = Field(min_length=1)
    responsibilities: list[str] = Field(min_length=1)
    boundaries: list[str] = Field(min_length=1)
    entrypoints: list[Entry] = Field(default_factory=list[Entry])
    files: list[FileEntry] = Field(default_factory=list[FileEntry])
    dependencies: Dependencies = Field(default_factory=Dependencies)
    submodules: list[Submodule] = Field(default_factory=list[Submodule])
    contracts: list[Contract] = Field(default_factory=list[Contract])
    tests: Tests | None = None


def resolve_reference(root: Path, base: Path, value: str) -> Path:
    """Resolve a literal local reference, rejecting absolute and escaping paths."""
    if not value.strip() or Path(value).is_absolute():
        raise ValueError(f"Expected nonempty relative path: {value!r}")
    target = (base / value).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"Reference escapes repository: {value!r}")
    return target


def validate_manifests(root: Path, paths: list[Path]) -> list[str]:
    """Check schema, local references, reciprocal ownership and root reachability."""
    root = root.resolve()
    errors: list[str] = []
    documents: dict[Path, Structure] = {}
    for path in sorted(set(paths)):
        path = path.resolve()
        if not path.is_relative_to(root):
            errors.append(f"Manifest escapes repository: {path}")
            continue
        try:
            documents[path] = Structure.model_validate(
                yaml.safe_load(path.read_text(encoding="utf-8"))
            )
        except (OSError, UnicodeError, yaml.YAMLError, ValidationError) as exc:
            errors.append(f"{path.relative_to(root)}: {exc}")

    def reference(
        base: Path, value: str, kind: Literal["file", "directory"]
    ) -> Path | None:
        try:
            target = resolve_reference(root, base, value)
        except ValueError as exc:
            errors.append(f"{base.relative_to(root)}: {exc}")
            return None
        exists = target.is_file() if kind == "file" else target.is_dir()
        if not exists:
            errors.append(f"Missing {kind}: {target.relative_to(root)}")
        return target

    root_manifest = root / "structure.yml"
    if root_manifest not in documents:
        errors.append("Missing or invalid root structure.yml")
    edges: dict[Path, list[Path]] = {}
    owners: dict[Path, Path] = {}
    for path, document in documents.items():
        directory = path.parent
        if reference(root, document.path, "directory") != directory:
            errors.append(
                f"{path.relative_to(root)}: path must identify its containing directory"
            )
        if path == root_manifest:
            if document.tier != "root" or document.parent is not None:
                errors.append("Root requires tier: root and parent: null")
        elif document.tier == "root" or document.parent is None:
            errors.append(
                f"{path.relative_to(root)}: non-root requires a parent and non-root tier"
            )
        else:
            reference(directory, document.parent, "file")
        for item in document.entrypoints:
            target = reference(directory, item.file, "file")
            if target is not None and not target.is_relative_to(directory):
                errors.append(
                    f"{path.relative_to(root)}: entrypoint outside local scope"
                )
        for item in document.files:
            target = reference(directory, item.file, "file")
            if target is not None and not target.is_relative_to(directory):
                errors.append(f"{path.relative_to(root)}: file outside local scope")
        for dependency in document.dependencies.internal:
            # Dependencies may identify a module file or package directory.
            try:
                target = resolve_reference(root, root, dependency)
                if not target.exists():
                    errors.append(f"Missing internal dependency: {dependency}")
            except ValueError as exc:
                errors.append(str(exc))
        for contract in document.contracts:
            reference(root, contract.doc, "file")
        if document.tests is not None:
            reference(root, document.tests.directory, "directory")
            for test in document.tests.files:
                reference(root, test, "file")
        edges[path] = []
        names: set[str] = set()
        for submodule in document.submodules:
            if submodule.name in names:
                errors.append(
                    f"{path.relative_to(root)}: duplicate submodule {submodule.name}"
                )
            names.add(submodule.name)
            child_directory = reference(directory, submodule.path, "directory")
            child = reference(directory, submodule.manifest, "file")
            if child is None or child_directory is None:
                continue
            if child_directory == directory or not child_directory.is_relative_to(
                directory
            ):
                errors.append(
                    f"{path.relative_to(root)}: submodule must be a strict descendant"
                )
            if child != child_directory / "structure.yml":
                errors.append(
                    f"{path.relative_to(root)}: submodule manifest does not match path"
                )
            if child in owners:
                errors.append(f"{child.relative_to(root)}: duplicate parent link")
            owners[child] = path
            edges[path].append(child)
            child_document = documents.get(child)
            if child_document is None:
                errors.append(
                    f"{child.relative_to(root)}: linked manifest missing or invalid"
                )
            elif (
                child_document.parent is None
                or reference(child.parent, child_document.parent, "file") != path
            ):
                errors.append(
                    f"{child.relative_to(root)}: parent link is not reciprocal"
                )

    visited: set[Path] = set()
    pending = [root_manifest]
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        pending.extend(edges.get(path, []))
    for path in documents.keys() - visited:
        errors.append(f"Orphan manifest: {path.relative_to(root)}")
    return errors


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    # Git limits discovery to project files, avoiding venvs, caches and clinical storage.
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    paths = sorted(
        {
            root / name
            for name in result.stdout.split("\0")
            if name and Path(name).name == "structure.yml"
        }
    )
    errors = validate_manifests(root, paths)
    if errors:
        print(
            "Structure validation failed:\n"
            + "\n".join(f"  - {error}" for error in errors)
        )
        return 1
    print(f"Successfully validated {len(paths)} structure.yml manifests.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

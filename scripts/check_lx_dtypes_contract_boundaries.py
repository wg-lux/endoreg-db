from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import re
import sys
import tomllib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = PROJECT_ROOT / "quality" / "lx_dtypes_contract_policy.yml"
DEFAULT_PYPROJECT = PROJECT_ROOT / "pyproject.toml"
_EXACT_PIN_PATTERN = re.compile(r"^lx-dtypes==(?P<version>[^;\s*]+)$", re.IGNORECASE)


class ContractBoundaryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    source_root: str = Field(min_length=1)
    dependency_name: Literal["lx-dtypes"]
    maximum_local_type_aliases: int = Field(ge=0)
    forbid_lx_dtypes_realiases: bool
    require_exact_dependency_pin: bool
    require_installed_version_match: bool
    canonical_import_prefixes: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_prefixes(self) -> "ContractBoundaryPolicy":
        if self.canonical_import_prefixes != sorted(
            set(self.canonical_import_prefixes)
        ):
            raise ValueError("canonical_import_prefixes must be sorted and unique")
        if not all(
            prefix.startswith("lx_dtypes.") for prefix in self.canonical_import_prefixes
        ):
            raise ValueError("canonical import prefixes must belong to lx_dtypes")
        return self


@dataclass(frozen=True, slots=True, order=True)
class LocalTypeAlias:
    source_path: str
    line: int
    name: str
    target: str


@dataclass(frozen=True, slots=True, order=True)
class LxDtypesRealias:
    source_path: str
    line: int
    local_name: str
    canonical_target: str


@dataclass(frozen=True, slots=True)
class ContractBoundaryReport:
    local_aliases: tuple[LocalTypeAlias, ...]
    lx_dtypes_realiases: tuple[LxDtypesRealias, ...]
    pinned_version: str | None
    installed_version: str | None
    errors: tuple[str, ...]

    @property
    def is_clean(self) -> bool:
        return not self.errors


def load_policy(path: Path) -> ContractBoundaryPolicy:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return ContractBoundaryPolicy.model_validate(raw)


def _alias_assignment(
    node: ast.AST,
) -> tuple[str, ast.expr] | None:
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        annotation = ast.unparse(node.annotation)
        if annotation in {"TypeAlias", "typing.TypeAlias"} and node.value is not None:
            return node.target.id, node.value
    type_alias_node = getattr(ast, "TypeAlias", None)
    if type_alias_node is not None and isinstance(node, type_alias_node):
        if isinstance(node.name, ast.Name):
            return node.name.id, node.value
    return None


def _lx_dtypes_imports(tree: ast.Module) -> dict[str, str]:
    imports: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            for imported in node.names:
                qualified = f"{node.module}.{imported.name}"
                if qualified == "lx_dtypes" or qualified.startswith("lx_dtypes."):
                    imports[imported.asname or imported.name] = qualified
        elif isinstance(node, ast.Import):
            for imported in node.names:
                if imported.name == "lx_dtypes" or imported.name.startswith(
                    "lx_dtypes."
                ):
                    local_name = imported.asname or imported.name.split(".", 1)[0]
                    imports[local_name] = (
                        imported.name if imported.asname else local_name
                    )
    return imports


def _canonical_target(expression: ast.expr, imports: dict[str, str]) -> str | None:
    if isinstance(expression, ast.Name):
        return imports.get(expression.id)
    if not isinstance(expression, ast.Attribute):
        return None
    parts: list[str] = []
    current: ast.expr = expression
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name) or current.id not in imports:
        return None
    return ".".join((imports[current.id], *reversed(parts)))


def discover_local_type_aliases(source_root: Path) -> tuple[LocalTypeAlias, ...]:
    aliases: list[LocalTypeAlias] = []
    for source_path in sorted(source_root.rglob("*.py")):
        tree = ast.parse(
            source_path.read_text(encoding="utf-8"), filename=str(source_path)
        )
        for node in ast.walk(tree):
            assignment = _alias_assignment(node)
            if assignment is None:
                continue
            name, target = assignment
            aliases.append(
                LocalTypeAlias(
                    source_path=source_path.relative_to(PROJECT_ROOT).as_posix()
                    if source_path.is_relative_to(PROJECT_ROOT)
                    else source_path.as_posix(),
                    line=node.lineno,
                    name=name,
                    target=ast.unparse(target),
                )
            )
    return tuple(sorted(aliases))


def discover_lx_dtypes_realiases(source_root: Path) -> tuple[LxDtypesRealias, ...]:
    realiases: list[LxDtypesRealias] = []
    for source_path in sorted(source_root.rglob("*.py")):
        tree = ast.parse(
            source_path.read_text(encoding="utf-8"), filename=str(source_path)
        )
        imports = _lx_dtypes_imports(tree)
        for node in ast.walk(tree):
            assignment = _alias_assignment(node)
            if assignment is None:
                continue
            local_name, target = assignment
            canonical_target = _canonical_target(target, imports)
            if canonical_target is None:
                continue
            realiases.append(
                LxDtypesRealias(
                    source_path=source_path.relative_to(PROJECT_ROOT).as_posix()
                    if source_path.is_relative_to(PROJECT_ROOT)
                    else source_path.as_posix(),
                    line=node.lineno,
                    local_name=local_name,
                    canonical_target=canonical_target,
                )
            )
    return tuple(sorted(realiases))


def exact_dependency_pin(pyproject_path: Path) -> str | None:
    payload = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    dependencies = payload.get("project", {}).get("dependencies", [])
    if not isinstance(dependencies, list):
        return None
    for dependency in dependencies:
        if not isinstance(dependency, str):
            continue
        match = _EXACT_PIN_PATTERN.fullmatch(dependency.strip())
        if match is not None:
            return match.group("version")
    return None


def check_contract_boundaries(
    policy: ContractBoundaryPolicy,
    *,
    project_root: Path = PROJECT_ROOT,
    pyproject_path: Path = DEFAULT_PYPROJECT,
    installed_version: str | None = None,
) -> ContractBoundaryReport:
    source_root = project_root / policy.source_root
    aliases = discover_local_type_aliases(source_root)
    realiases = discover_lx_dtypes_realiases(source_root)
    pinned_version = exact_dependency_pin(pyproject_path)
    errors: list[str] = []
    if len(aliases) > policy.maximum_local_type_aliases:
        errors.append(
            "local type alias budget exceeded: "
            f"{len(aliases)} > {policy.maximum_local_type_aliases}"
        )
    if policy.forbid_lx_dtypes_realiases:
        errors.extend(
            f"{item.source_path}:{item.line} {item.local_name} re-aliases "
            f"{item.canonical_target}; import and use the canonical type directly"
            for item in realiases
        )
    if policy.require_exact_dependency_pin and pinned_version is None:
        errors.append("pyproject.toml must pin lx-dtypes with one exact == version")
    for module_name in policy.canonical_import_prefixes:
        try:
            import_module(module_name)
        except ImportError as exc:
            errors.append(
                f"canonical lx-dtypes module is unavailable: {module_name} ({exc})"
            )
    resolved_installed_version = installed_version
    if policy.require_installed_version_match and resolved_installed_version is None:
        try:
            resolved_installed_version = version(policy.dependency_name)
        except PackageNotFoundError:
            errors.append("the pinned lx-dtypes package is not installed")
    if (
        policy.require_installed_version_match
        and pinned_version is not None
        and resolved_installed_version is not None
        and resolved_installed_version != pinned_version
    ):
        errors.append(
            "installed lx-dtypes version does not match the project contract pin: "
            f"{resolved_installed_version} != {pinned_version}"
        )
    return ContractBoundaryReport(
        local_aliases=aliases,
        lx_dtypes_realiases=realiases,
        pinned_version=pinned_version,
        installed_version=resolved_installed_version,
        errors=tuple(errors),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Enforce the canonical lx-dtypes contract and type-alias boundary."
    )
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--pyproject", type=Path, default=DEFAULT_PYPROJECT)
    args = parser.parse_args()
    policy = load_policy(args.policy.resolve())
    report = check_contract_boundaries(
        policy,
        project_root=PROJECT_ROOT,
        pyproject_path=args.pyproject.resolve(),
    )
    for error in report.errors:
        print(f"LX_DTYPES_CONTRACT {error}")
    if not report.is_clean:
        return 1
    print(
        "lx-dtypes contract boundary clean: "
        f"version={report.pinned_version}, local_type_aliases="
        f"{len(report.local_aliases)}/{policy.maximum_local_type_aliases}, "
        "lx_dtypes_realiases=0."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

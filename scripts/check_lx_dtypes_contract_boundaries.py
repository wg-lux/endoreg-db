from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Literal

import yaml
from lx_dtypes.knowledge_bases import (
    PackagedKnowledgeBase,
    PackagedKnowledgeBaseResourceError,
    list_packaged_knowledge_bases,
)
from pydantic import BaseModel, ConfigDict, Field, model_validator

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = PROJECT_ROOT / "quality" / "lx_dtypes_contract_policy.yml"


class ContractBoundaryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    source_root: str = Field(min_length=1)
    maximum_local_type_aliases: int = Field(ge=0)
    canonical_local_types: dict[str, str] = Field(default_factory=dict)
    canonical_type_imports: dict[str, str] = Field(default_factory=dict)
    canonical_alias_shapes: dict[str, str] = Field(default_factory=dict)
    inline_alias_shapes: list[str] = Field(default_factory=list)
    forbid_lx_dtypes_realiases: bool
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
    terminologies: tuple[PackagedKnowledgeBase, ...]
    lx_dtypes_realiases: tuple[LxDtypesRealias, ...]
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
    if isinstance(node, ast.TypeAlias):
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
    for source_path in sorted(
        [*source_root.rglob("*.py"), *source_root.rglob("*.pyi")]
    ):
        tree = ast.parse(
            source_path.read_text(encoding="utf-8"), filename=str(source_path)
        )
        for node in ast.walk(tree):
            if not isinstance(node, (ast.AnnAssign, ast.TypeAlias, ast.ClassDef)):
                continue
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
    for source_path in sorted(
        [*source_root.rglob("*.py"), *source_root.rglob("*.pyi")]
    ):
        tree = ast.parse(
            source_path.read_text(encoding="utf-8"), filename=str(source_path)
        )
        imports = _lx_dtypes_imports(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.AnnAssign, ast.TypeAlias, ast.ClassDef)):
                continue
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


def check_contract_boundaries(
    policy: ContractBoundaryPolicy,
    *,
    project_root: Path = PROJECT_ROOT,
) -> ContractBoundaryReport:
    source_root = project_root / policy.source_root
    aliases = discover_local_type_aliases(source_root)
    realiases = discover_lx_dtypes_realiases(source_root)
    errors: list[str] = []
    errors.extend(
        f"{item.source_path}:{item.line} {item.name} renames None; use None directly"
        for item in aliases
        if item.target in {"None", "'None'", '"None"'}
    )
    inline_shapes = {
        _type_shape(ast.parse(expression, mode="eval").body)
        for expression in policy.inline_alias_shapes
    }
    for item in aliases:
        if _type_shape(ast.parse(item.target, mode="eval").body) in inline_shapes:
            errors.append(
                f"{item.source_path}:{item.line} {item.name} is a trivial alias; "
                "use the underlying type directly"
            )
    for source_path in sorted(
        [*source_root.rglob("*.py"), *source_root.rglob("*.pyi")]
    ):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        relative_path = source_path.relative_to(source_root).as_posix()
        for node in ast.walk(tree):
            if not isinstance(
                node, (ast.Assign, ast.AnnAssign, ast.TypeAlias, ast.ClassDef)
            ):
                continue
            assignment = _alias_assignment(node)
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                assignment = (node.targets[0].id, node.value)
            declaration_name = (
                assignment[0]
                if assignment is not None
                else node.name
                if isinstance(node, ast.ClassDef)
                else None
            )
            if declaration_name in policy.canonical_local_types:
                owner = policy.canonical_local_types[declaration_name]
                if relative_path != owner:
                    errors.append(
                        f"{source_path}:{node.lineno} {declaration_name} belongs to {owner}"
                    )
            if declaration_name in policy.canonical_type_imports:
                errors.append(
                    f"{source_path}:{node.lineno} import {declaration_name} from "
                    f"{policy.canonical_type_imports[declaration_name]}"
                )
            if assignment is not None:
                shape = _type_shape(assignment[1], self_name=assignment[0])
                for expression, canonical in policy.canonical_alias_shapes.items():
                    if shape != _type_shape(ast.parse(expression, mode="eval").body):
                        continue
                    module, name = canonical.rsplit(".", 1)
                    owner_path = (
                        module.removeprefix("endoreg_db.").replace(".", "/") + ".py"
                    )
                    if relative_path != owner_path or assignment[0] != name:
                        errors.append(
                            f"{source_path}:{node.lineno} {assignment[0]} duplicates {canonical}"
                        )
            if (
                assignment is not None
                and ast.unparse(assignment[1]) in {"File", "File[bytes]"}
                and relative_path != "helpers/typing.py"
            ):
                errors.append(
                    f"{source_path}:{node.lineno} import DjangoFile from helpers.typing"
                )
            if not isinstance(node, ast.ClassDef):
                continue
            members = [
                member
                for member in node.body
                if not (
                    isinstance(member, ast.Expr)
                    and isinstance(member.value, ast.Constant)
                )
            ]
            if (
                any(
                    ast.unparse(base) in {"TypedDict", "typing.TypedDict"}
                    for base in node.bases
                )
                and len(members) == 1
                and isinstance(members[0], ast.AnnAssign)
                and ast.unparse(members[0].target) == "verbose"
                and ast.unparse(members[0].annotation) == "bool"
            ):
                errors.append(
                    f"{source_path}:{node.lineno} validate command options with "
                    "VerboseManagementCommandOptionsPayload instead of duplicating its shape"
                )
            if (
                relative_path != "helpers/typing.py"
                and any(
                    ast.unparse(base) in {"Protocol", "typing.Protocol"}
                    for base in node.bases
                )
                and len(members) == 1
                and isinstance(members[0], ast.FunctionDef)
                and members[0].name == "save"
                and any(
                    arg.arg == "content"
                    and arg.annotation is not None
                    and ast.unparse(arg.annotation) in {"DjangoFile", "File[bytes]"}
                    for arg in members[0].args.args
                )
            ):
                errors.append(
                    f"{source_path}:{node.lineno} import BinaryFieldFileSaver from helpers.typing"
                )
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
    for module_name in policy.canonical_import_prefixes:
        try:
            import_module(module_name)
        except ImportError as exc:
            errors.append(
                f"canonical lx-dtypes module is unavailable: {module_name} ({exc})"
            )
    terminologies: tuple[PackagedKnowledgeBase, ...] = ()
    try:
        terminologies = list_packaged_knowledge_bases()
        if not terminologies:
            errors.append("lx-dtypes provides no packaged terminology versions")
        for terminology in terminologies:
            terminology.verified_resource_directory()
    except (OSError, ValueError, PackagedKnowledgeBaseResourceError) as exc:
        errors.append(f"packaged terminology version is unavailable or invalid: {exc}")
    return ContractBoundaryReport(
        local_aliases=aliases,
        lx_dtypes_realiases=realiases,
        terminologies=terminologies,
        errors=tuple(errors),
    )


def _type_shape(expression: ast.expr, *, self_name: str | None = None) -> str:
    """Compare aliases independent of union order and forward-reference spelling."""
    if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
        try:
            return _type_shape(
                ast.parse(expression.value, mode="eval").body, self_name=self_name
            )
        except SyntaxError:
            return ast.unparse(expression)
    if isinstance(expression, ast.BinOp) and isinstance(expression.op, ast.BitOr):
        pending: list[ast.expr] = [expression]
        members: set[str] = set()
        while pending:
            member = pending.pop()
            if isinstance(member, ast.BinOp) and isinstance(member.op, ast.BitOr):
                pending.extend((member.left, member.right))
            else:
                members.add(_type_shape(member, self_name=self_name))
        return " | ".join(sorted(members))
    if isinstance(expression, ast.Subscript) and ast.unparse(expression.value) in {
        "Literal",
        "typing.Literal",
    }:
        values = (
            expression.slice.elts
            if isinstance(expression.slice, ast.Tuple)
            else [expression.slice]
        )
        return (
            "Literal[" + ", ".join(sorted(ast.unparse(value) for value in values)) + "]"
        )
    if isinstance(expression, ast.Name) and expression.id == self_name:
        return "Self"
    if isinstance(expression, ast.Subscript):
        return (
            _type_shape(expression.value, self_name=self_name)
            + "["
            + _type_shape(expression.slice, self_name=self_name)
            + "]"
        )
    if isinstance(expression, ast.Tuple):
        return ", ".join(
            _type_shape(item, self_name=self_name) for item in expression.elts
        )
    return ast.unparse(expression)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Enforce the canonical lx-dtypes contract and type-alias boundary."
    )
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    args = parser.parse_args()
    policy = load_policy(args.policy.resolve())
    report = check_contract_boundaries(
        policy,
        project_root=PROJECT_ROOT,
    )
    for error in report.errors:
        print(f"LX_DTYPES_CONTRACT {error}")
    if not report.is_clean:
        return 1
    print(
        "lx-dtypes contract boundary clean: "
        f"packaged_terminology_versions={len(report.terminologies)}, local_type_aliases="
        f"{len(report.local_aliases)}/{policy.maximum_local_type_aliases}, "
        "lx_dtypes_realiases=0."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

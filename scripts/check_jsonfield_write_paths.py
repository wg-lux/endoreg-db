from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass, field
from pathlib import Path
import re
import sys

from scripts.check_lx_dtypes_model_inventory import (
    DEFAULT_INVENTORY,
    ModelInventory,
    ModelKind,
    ModelTarget,
    load_inventory,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_ROOT = PROJECT_ROOT / "endoreg_db"
_BYPASS_OPERATIONS = frozenset({"bulk_create", "bulk_update", "update"})
_VALIDATOR_PREFIXES = ("canonicalize_", "dump_", "normalize_", "validate_")
_SUPPRESSION_PATTERN = re.compile(
    r"#\s*jsonfield-write-guard:\s*validated\s*--\s*(?P<reason>\S.*)$"
)


@dataclass(frozen=True, slots=True)
class StandardizedJsonModel:
    label: str
    fields: frozenset[str]


@dataclass(frozen=True, slots=True)
class JsonFieldWriteIssue:
    path: str
    line: int
    model_label: str
    operation: str
    fields: tuple[str, ...]

    def render(self) -> str:
        field_list = ", ".join(self.fields)
        return (
            f"{self.path}:{self.line}: unvalidated {self.operation} bypass for "
            f"{self.model_label} JSONField(s): {field_list}"
        )


@dataclass(slots=True)
class _ScopeState:
    model_aliases: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    expressions: dict[str, ast.expr] = field(
        default_factory=lambda: dict[str, ast.expr]()
    )
    mappings: dict[str, dict[str, ast.expr]] = field(
        default_factory=lambda: dict[str, dict[str, ast.expr]]()
    )
    validated_names: set[str] = field(default_factory=lambda: set[str]())

    def child(self) -> _ScopeState:
        return _ScopeState(model_aliases=dict(self.model_aliases))


def standardized_json_models(
    inventory: ModelInventory,
) -> dict[str, StandardizedJsonModel]:
    """Return unambiguous class-name mappings for guarded concrete models."""

    guarded_targets = {
        ModelTarget.LOCAL_BOUNDARY_SCHEMA,
        ModelTarget.SHARED_LX_DTYPES_CONTRACT,
    }
    result: dict[str, StandardizedJsonModel] = {}
    for entry in inventory.models:
        if (
            entry.kind is not ModelKind.REGISTERED
            or entry.target not in guarded_targets
            or not entry.json_fields
        ):
            continue
        class_name = entry.label.rsplit(".", 1)[-1]
        if class_name in result:
            raise ValueError(
                "JSONField write guard requires unique model class names; "
                f"found duplicate {class_name}"
            )
        result[class_name] = StandardizedJsonModel(
            label=entry.label,
            fields=frozenset(entry.json_fields),
        )
    return result


def _call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_contract_expression(node: ast.expr, state: _ScopeState) -> bool:
    if isinstance(node, ast.Name):
        return node.id in state.validated_names
    if not isinstance(node, ast.Call):
        return False

    call_name = _call_name(node.func)
    if call_name is not None and call_name.startswith(_VALIDATOR_PREFIXES):
        return True
    if call_name == "model_validate":
        return True
    if isinstance(node.func, ast.Attribute):
        return _is_contract_expression(node.func.value, state)
    return False


def _string_mapping(node: ast.expr) -> dict[str, ast.expr] | None:
    if not isinstance(node, ast.Dict):
        return None
    result: dict[str, ast.expr] = {}
    for key, value in zip(node.keys, node.values, strict=True):
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            return None
        result[key.value] = value
    return result


def _historical_model_name(node: ast.expr) -> str | None:
    if not isinstance(node, ast.Call) or _call_name(node.func) != "get_model":
        return None
    string_args = [
        arg.value
        for arg in node.args
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
    ]
    if len(string_args) >= 2:
        return string_args[1]
    if len(string_args) == 1 and "." in string_args[0]:
        return string_args[0].rsplit(".", 1)[-1]
    return None


class _JsonFieldWriteVisitor(ast.NodeVisitor):
    def __init__(
        self,
        *,
        path: str,
        source_lines: list[str],
        models: dict[str, StandardizedJsonModel],
    ) -> None:
        self._path = path
        self._source_lines = source_lines
        self._models = models
        self._scopes = [_ScopeState()]
        self.issues: list[JsonFieldWriteIssue] = []

    @property
    def _state(self) -> _ScopeState:
        return self._scopes[-1]

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name in self._models:
                self._state.model_aliases[alias.asname or alias.name] = alias.name

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._scopes.append(self._state.child())
        for statement in node.body:
            self.visit(statement)
        self._scopes.pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        self._record_assignment(node.targets, node.value)
        self.generic_visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self._record_assignment([node.target], node.value)
            self.generic_visit(node.value)

    def _record_assignment(self, targets: list[ast.expr], value: ast.expr) -> None:
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            name = target.id
            self._state.expressions[name] = value
            historical_model = _historical_model_name(value)
            if historical_model in self._models:
                self._state.model_aliases[name] = historical_model
            mapping = _string_mapping(value)
            if mapping is not None:
                self._state.mappings[name] = mapping
            else:
                self._state.mappings.pop(name, None)
            if _is_contract_expression(value, self._state):
                self._state.validated_names.add(name)
            else:
                self._state.validated_names.discard(name)

    def visit_Call(self, node: ast.Call) -> None:
        self._record_mapping_update(node)
        operation = _call_name(node.func)
        if operation not in _BYPASS_OPERATIONS or not isinstance(
            node.func, ast.Attribute
        ):
            self.generic_visit(node)
            return

        model_name = self._manager_model_name(node.func.value)
        model = self._models.get(model_name or "")
        if model is None or self._is_suppressed(node.lineno):
            self.generic_visit(node)
            return

        if operation == "update":
            unsafe_fields = self._unsafe_update_fields(node, model)
        elif operation == "bulk_update":
            unsafe_fields = self._bulk_update_fields(node, model)
        else:
            unsafe_fields = self._unsafe_bulk_create_fields(node, model_name, model)

        if unsafe_fields:
            self.issues.append(
                JsonFieldWriteIssue(
                    path=self._path,
                    line=node.lineno,
                    model_label=model.label,
                    operation=operation,
                    fields=tuple(sorted(unsafe_fields)),
                )
            )
        self.generic_visit(node)

    def _record_mapping_update(self, node: ast.Call) -> None:
        if (
            not isinstance(node.func, ast.Attribute)
            or node.func.attr != "update"
            or not isinstance(node.func.value, ast.Name)
            or not node.args
        ):
            return
        mapping = _string_mapping(node.args[0])
        if mapping is not None and node.func.value.id in self._state.mappings:
            self._state.mappings[node.func.value.id].update(mapping)

    def _manager_model_name(self, node: ast.expr) -> str | None:
        current = node
        while True:
            if isinstance(current, ast.Attribute):
                if current.attr == "objects":
                    if isinstance(current.value, ast.Name):
                        return self._state.model_aliases.get(
                            current.value.id, current.value.id
                        )
                    return None
                current = current.value
                continue
            if isinstance(current, ast.Call) and isinstance(
                current.func, ast.Attribute
            ):
                current = current.func.value
                continue
            return None

    def _expanded_keywords(self, node: ast.Call) -> dict[str, ast.expr]:
        values: dict[str, ast.expr] = {}
        for keyword in node.keywords:
            if keyword.arg is not None:
                values[keyword.arg] = keyword.value
                continue
            mapping = _string_mapping(keyword.value)
            if mapping is None and isinstance(keyword.value, ast.Name):
                mapping = self._state.mappings.get(keyword.value.id)
            if mapping is not None:
                values.update(mapping)
        return values

    def _unsafe_update_fields(
        self, node: ast.Call, model: StandardizedJsonModel
    ) -> set[str]:
        values = self._expanded_keywords(node)
        return {
            field_name
            for field_name in model.fields & values.keys()
            if not _is_contract_expression(values[field_name], self._state)
        }

    def _bulk_update_fields(
        self, node: ast.Call, model: StandardizedJsonModel
    ) -> set[str]:
        fields_node: ast.expr | None = node.args[1] if len(node.args) > 1 else None
        for keyword in node.keywords:
            if keyword.arg == "fields":
                fields_node = keyword.value
        if isinstance(fields_node, ast.Name):
            fields_node = self._state.expressions.get(fields_node.id)
        if not isinstance(fields_node, (ast.List, ast.Tuple, ast.Set)):
            return set()
        field_names = {
            item.value
            for item in fields_node.elts
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        }
        return set(model.fields & field_names)

    def _unsafe_bulk_create_fields(
        self,
        node: ast.Call,
        model_name: str | None,
        model: StandardizedJsonModel,
    ) -> set[str]:
        if not node.args or model_name is None:
            return set()
        return self._unsafe_constructor_fields(
            node.args[0], model_name=model_name, model=model, seen=set()
        )

    def _unsafe_constructor_fields(
        self,
        node: ast.expr,
        *,
        model_name: str,
        model: StandardizedJsonModel,
        seen: set[str],
    ) -> set[str]:
        if isinstance(node, ast.Name):
            if node.id in seen:
                return set()
            expression = self._state.expressions.get(node.id)
            if expression is None:
                return set()
            return self._unsafe_constructor_fields(
                expression,
                model_name=model_name,
                model=model,
                seen=seen | {node.id},
            )
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            unsafe_fields: set[str] = set()
            for item in node.elts:
                unsafe_fields.update(
                    self._unsafe_constructor_fields(
                        item,
                        model_name=model_name,
                        model=model,
                        seen=seen,
                    )
                )
            return unsafe_fields
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            return self._unsafe_constructor_fields(
                node.elt,
                model_name=model_name,
                model=model,
                seen=seen,
            )
        if not isinstance(node, ast.Call):
            return set()
        constructor_name = _call_name(node.func)
        resolved_name = self._state.model_aliases.get(
            constructor_name or "", constructor_name
        )
        if resolved_name != model_name:
            return set()
        values = self._expanded_keywords(node)
        return {
            field_name
            for field_name in model.fields & values.keys()
            if not _is_contract_expression(values[field_name], self._state)
        }

    def _is_suppressed(self, line_number: int) -> bool:
        for index in (line_number - 2, line_number - 1):
            if index < 0 or index >= len(self._source_lines):
                continue
            if _SUPPRESSION_PATTERN.search(self._source_lines[index]):
                return True
        return False


def discover_unvalidated_jsonfield_writes(
    *,
    source_root: Path,
    models: dict[str, StandardizedJsonModel],
    project_root: Path | None = None,
) -> tuple[JsonFieldWriteIssue, ...]:
    effective_project_root = project_root or source_root.parent
    issues: list[JsonFieldWriteIssue] = []
    for path in sorted(source_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        try:
            relative_path = path.relative_to(effective_project_root).as_posix()
        except ValueError:
            relative_path = path.as_posix()
        visitor = _JsonFieldWriteVisitor(
            path=relative_path,
            source_lines=source.splitlines(),
            models=models,
        )
        visitor.visit(tree)
        issues.extend(visitor.issues)
    return tuple(sorted(issues, key=lambda issue: (issue.path, issue.line)))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Reject statically resolvable ORM bulk/update writes that bypass "
            "standardized JSONField validation."
        )
    )
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    args = parser.parse_args()

    inventory = load_inventory(args.inventory.resolve())
    models = standardized_json_models(inventory)
    issues = discover_unvalidated_jsonfield_writes(
        source_root=args.source_root.resolve(),
        models=models,
        project_root=PROJECT_ROOT,
    )
    for issue in issues:
        print(issue.render())
    if issues:
        print(
            "Validate/normalize each value before the ORM bypass, use normal "
            "model save(), or add '# jsonfield-write-guard: validated -- "
            "<reason>' for a reviewed false positive."
        )
        return 1
    print(
        f"JSONField write-path guard clean: {len(models)} standardized models scanned."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Generate the deterministic LXDM contract-to-consumer inventory."""

from __future__ import annotations

import argparse
import ast
from collections import defaultdict
from pathlib import Path

CONTRACT_PACKAGE = "lx_dtypes.models.contracts"


def _python_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.py")
        if not {".devenv", ".git", "__pycache__"}.intersection(path.parts)
    )


def _literal_all(tree: ast.Module) -> list[str]:
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in targets
        ):
            continue
        value = node.value
        if isinstance(value, (ast.List, ast.Tuple)):
            return [
                item.value
                for item in value.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            ]
    return []


def _defined_public_symbols(tree: ast.Module) -> list[str]:
    symbols: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                symbols.add(node.name)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if not node.target.id.startswith("_"):
                symbols.add(node.target.id)
    return sorted(symbols)


def _package_reexports(init_path: Path) -> dict[str, set[str]]:
    tree = ast.parse(init_path.read_text(encoding="utf-8"), filename=str(init_path))
    allowed = set(_literal_all(tree))
    result: dict[str, set[str]] = defaultdict(set)
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or node.level != 1 or not node.module:
            continue
        for alias in node.names:
            public_name = alias.asname or alias.name
            if not allowed or public_name in allowed:
                result[node.module].add(public_name)
    return result


def _imports_by_contract(
    files: list[Path], exports: dict[str, set[str]], repo: Path
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    direct: dict[str, set[str]] = defaultdict(set)
    dynamic: dict[str, set[str]] = defaultdict(set)
    symbol_modules: dict[str, set[str]] = defaultdict(set)
    for module, names in exports.items():
        for name in names:
            symbol_modules[name].add(module)

    for path in files:
        relative = str(path.relative_to(repo))
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module == CONTRACT_PACKAGE:
                    for alias in node.names:
                        for module in symbol_modules.get(alias.name, set()):
                            direct[module].add(relative)
                elif node.module and node.module.startswith(f"{CONTRACT_PACKAGE}."):
                    module = node.module.removeprefix(f"{CONTRACT_PACKAGE}.").split(
                        "."
                    )[0]
                    direct[module].add(relative)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(f"{CONTRACT_PACKAGE}."):
                        module = alias.name.removeprefix(f"{CONTRACT_PACKAGE}.").split(
                            "."
                        )[0]
                        direct[module].add(relative)
        for module in exports:
            if (
                f"{CONTRACT_PACKAGE}.{module}" in text
                and relative not in direct[module]
            ):
                dynamic[module].add(relative)
    return direct, dynamic


def _boundaries(paths: set[str]) -> str:
    categories: set[str] = set()
    for path in paths:
        lowered = path.lower()
        if any(token in lowered for token in ("api/", "views/", "serializers/")):
            categories.add("API")
        if any(token in lowered for token in ("models/", "schemas/", "migrations/")):
            categories.add("persistence")
        if any(
            token in lowered for token in ("hub/", "import", "export", "task", "job")
        ):
            categories.add("job/import-export")
        if any(token in lowered for token in ("auth", "permission", "security")):
            categories.add("auth")
        if any(token in lowered for token in ("media", "video", "frame", "pdf")):
            categories.add("media")
        if "/tests/" in f"/{lowered}" or lowered.startswith("tests/"):
            categories.add("test")
    return ", ".join(sorted(categories)) or "unassigned"


def _invariants(text: str) -> str:
    values: list[str] = []
    if 'extra="forbid"' in text or "extra='forbid'" in text:
        values.append("unknown fields forbidden")
    elif 'extra="allow"' in text or "extra='allow'" in text:
        values.append("compatibility extras allowed")
    if "strict=True" in text:
        values.append("strict Pydantic mode")
    if any(token in text for token in ("field_validator", "model_validator")):
        values.append("semantic validators")
    if "Literal[" in text or "Enum" in text:
        values.append("closed values")
    helpers = [
        prefix for prefix in ("validate_", "parse_", "dump_") if f"def {prefix}" in text
    ]
    if helpers:
        values.append("validated parse/dump helpers")
    return "; ".join(values) or "typed field shape; inspect module validators"


def _short_paths(paths: set[str], limit: int = 4) -> str:
    ordered = sorted(paths)
    shown = ordered[:limit]
    suffix = f" (+{len(ordered) - limit})" if len(ordered) > limit else ""
    return "<br>".join(f"`{path}`" for path in shown) + suffix if shown else "—"


def _escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def generate(lxdm_root: Path, endoreg_root: Path) -> str:
    contracts_root = lxdm_root / "lx_dtypes/models/contracts"
    modules = sorted(
        path for path in contracts_root.glob("*.py") if path.name != "__init__.py"
    )
    reexports = _package_reexports(contracts_root / "__init__.py")
    exports: dict[str, set[str]] = {}
    source_text: dict[str, str] = {}
    for path in modules:
        module = path.stem
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        declared = set(_literal_all(tree) or _defined_public_symbols(tree))
        exports[module] = declared | reexports.get(module, set())
        source_text[module] = text

    direct, dynamic = _imports_by_contract(
        _python_files(endoreg_root / "endoreg_db")
        + _python_files(endoreg_root / "tests"),
        exports,
        endoreg_root,
    )
    test_files = _python_files(contracts_root / "tests")
    tests_by_module: dict[str, set[str]] = defaultdict(set)
    for test_path in test_files:
        text = test_path.read_text(encoding="utf-8", errors="replace")
        for module, names in exports.items():
            if module in test_path.stem or any(name in text for name in names):
                tests_by_module[module].add(str(test_path.relative_to(lxdm_root)))

    rows: list[str] = []
    for module in sorted(exports):
        production = {path for path in direct[module] if not path.startswith("tests/")}
        all_consumers = direct[module] | dynamic[module]
        if production:
            classification = "direct_use"
            risk = "Review host adapters for duplicated local validation; keep package version pinned."
            action = "Retain direct contract use; migrate remaining same-boundary dicts when touched."
        elif direct[module]:
            classification = "candidate_for_adoption"
            risk = "Referenced only by tests; no confirmed production boundary."
            action = "Confirm intended boundary before adoption or mark as LXDM-only."
        else:
            classification = "unused_or_uncertain"
            risk = "No Endoreg import found; semantic duplicates require owner review."
            action = "Do not adopt speculatively; assess in a bounded domain cohort."
        if dynamic[module] and not direct[module]:
            classification = "adapter_required"
            risk = "Only dynamic/string reference found; runtime validation may be implicit."
            action = "Add an explicit typed adapter and focused boundary test."

        symbols = sorted(exports[module])
        exported = "<br>".join(f"`{name}`" for name in symbols) or "—"
        tests = tests_by_module[module]
        verification = (
            _short_paths(tests, limit=2)
            if tests
            else "Pyright + nearest Endoreg boundary test required"
        )
        current = _short_paths(direct[module])
        if dynamic[module]:
            current += f"<br>dynamic: {_short_paths(dynamic[module], limit=2)}"
        row = [
            f"`{module}`",
            exported,
            "lx-data-models",
            current,
            _boundaries(all_consumers),
            _invariants(source_text[module]),
            classification,
            risk,
            action,
            verification,
        ]
        rows.append("| " + " | ".join(_escape(item) for item in row) + " |")

    header = """# Generated LXDM contract-to-consumer inventory

This file is generated by `feature-tracking/audit_lxdm_contracts.py`. It covers
every non-test module directly under `lx_dtypes/models/contracts`, its public
symbols/re-exports, mechanically detectable invariants and tests, and static or
dynamic references in Endoreg. Absence of an import is classified as uncertain,
not as proof that the contract is obsolete.

Regenerate from `/home/admin/endoreg-db`:

```bash
.devenv/state/venv/bin/python feature-tracking/audit_lxdm_contracts.py \\
  --lxdm-root /home/admin/lx-data-models \\
  --output docs/lxdm_contract_inventory.md
```

| contract_module | public_type / re-export | contract_owner | endoreg_consumer / current_shape | boundary | invariants | classification | gap_or_risk | recommended_action | verification |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
"""
    summary = f"\n\nInventory total: **{len(modules)} modules**.\n"
    return header + "\n".join(rows) + summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--lxdm-root", type=Path, default=Path("/home/admin/lx-data-models")
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    content = generate(args.lxdm_root.resolve(), Path.cwd().resolve())
    if args.output:
        args.output.write_text(content, encoding="utf-8")
    else:
        print(content, end="")


if __name__ == "__main__":
    main()

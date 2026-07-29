from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from importlib import import_module
import os
from pathlib import Path
import sys
from typing import cast, Literal, Protocol

import django
from django.apps import apps
from django.db import models
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = PROJECT_ROOT / "quality" / "lx_dtypes_model_inventory.yml"
DEFAULT_SETTINGS_MODULE = "endoreg_db.config.settings.test"


class _ModelMeta(Protocol):
    label: str
    db_table: str
    abstract: bool
    proxy: bool
    managed: bool
    local_fields: list[models.Field[object, object]]


class ModelTarget(StrEnum):
    SHARED_LX_DTYPES_CONTRACT = "shared_lx_dtypes_contract"
    LOCAL_BOUNDARY_SCHEMA = "local_boundary_schema"
    PERSISTENCE_ONLY = "persistence_only"
    TEMPORARY_EXCEPTION = "temporary_exception"
    UNCLASSIFIED = "unclassified"


class ModelKind(StrEnum):
    REGISTERED = "registered"
    ABSTRACT = "abstract"


class ModelInventoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)
    kind: ModelKind
    python_path: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    base_classes: list[str] = Field(min_length=1)
    db_table: str = Field(min_length=1)
    abstract: bool
    proxy: bool
    managed: bool
    json_fields: list[str] = Field(default_factory=list)
    custom_clean: bool
    custom_save: bool
    target: ModelTarget
    owner: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    ownership_evidence: list[str] = Field(default_factory=list)
    module_lx_dtypes_imports: list[str] = Field(default_factory=list)
    module_endoreg_schema_imports: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_lists(self) -> "ModelInventoryEntry":
        if self.json_fields != sorted(set(self.json_fields)):
            raise ValueError(f"{self.label} json_fields must be sorted and unique")
        if self.base_classes != sorted(set(self.base_classes)):
            raise ValueError(f"{self.label} base_classes must be sorted and unique")
        if self.ownership_evidence != sorted(set(self.ownership_evidence)):
            raise ValueError(
                f"{self.label} ownership_evidence must be sorted and unique"
            )
        if self.module_lx_dtypes_imports != sorted(
            set(self.module_lx_dtypes_imports)
        ):
            raise ValueError(
                f"{self.label} module_lx_dtypes_imports must be sorted and unique"
            )
        if self.module_endoreg_schema_imports != sorted(
            set(self.module_endoreg_schema_imports)
        ):
            raise ValueError(
                f"{self.label} module_endoreg_schema_imports must be sorted and unique"
            )
        if self.kind is ModelKind.REGISTERED and self.abstract:
            raise ValueError(f"{self.label} registered model cannot be abstract")
        if self.kind is ModelKind.ABSTRACT and not self.abstract:
            raise ValueError(f"{self.label} abstract inventory entry must be abstract")
        if (
            self.target is not ModelTarget.UNCLASSIFIED
            and not self.ownership_evidence
        ):
            raise ValueError(
                f"{self.label} classified target requires ownership_evidence"
            )
        return self


class ModelInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    feature_id: Literal["lx_dtypes_model_standardization"]
    models: list[ModelInventoryEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_models(self) -> "ModelInventory":
        labels = [item.label for item in self.models]
        if labels != sorted(labels):
            raise ValueError("model inventory entries must be sorted by label")
        if len(labels) != len(set(labels)):
            raise ValueError("model inventory contains duplicate labels")
        return self


@dataclass(frozen=True, slots=True)
class ModelInventoryComparison:
    new_models: tuple[ModelInventoryEntry, ...]
    stale_models: tuple[ModelInventoryEntry, ...]
    changed_models: tuple[
        tuple[ModelInventoryEntry, ModelInventoryEntry],
        ...,
    ]

    @property
    def is_clean(self) -> bool:
        return not (self.new_models or self.stale_models or self.changed_models)


def load_inventory(path: Path) -> ModelInventory:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return ModelInventory.model_validate(raw)


def _import_names(node: ast.ImportFrom | ast.Import) -> tuple[str, ...]:
    if isinstance(node, ast.ImportFrom):
        if node.module is None:
            return ()
        return tuple(f"{node.module}.{alias.name}" for alias in node.names)
    return tuple(alias.name for alias in node.names)


@lru_cache(maxsize=None)
def _source_contract_imports(
    source_path: Path,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    tree = ast.parse(
        source_path.read_text(encoding="utf-8"),
        filename=str(source_path),
    )
    imported_names = (
        name
        for node in ast.walk(tree)
        if isinstance(node, (ast.ImportFrom, ast.Import))
        for name in _import_names(node)
    )
    lx_dtypes: set[str] = set()
    endoreg_schemas: set[str] = set()
    for name in imported_names:
        if name == "lx_dtypes" or name.startswith("lx_dtypes."):
            lx_dtypes.add(name)
        elif name == "endoreg_db.schemas" or name.startswith(
            "endoreg_db.schemas."
        ):
            endoreg_schemas.add(name)
    return tuple(sorted(lx_dtypes)), tuple(sorted(endoreg_schemas))


def _entry_from_model(
    model: type[models.Model],
    *,
    kind: ModelKind,
) -> ModelInventoryEntry:
    meta = cast(_ModelMeta, model._meta)
    module = sys.modules.get(model.__module__)
    module_file = getattr(module, "__file__", None)
    if not isinstance(module_file, str):
        raise ValueError(f"Cannot resolve source file for {meta.label}")
    source_path = Path(module_file).resolve()
    try:
        relative_source_path = source_path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError as exc:
        raise ValueError(
            f"{meta.label} source is outside the repository: {source_path}"
        ) from exc
    lx_dtypes_imports, endoreg_schema_imports = _source_contract_imports(
        source_path
    )

    return ModelInventoryEntry(
        label=meta.label,
        kind=kind,
        python_path=f"{model.__module__}.{model.__qualname__}",
        source_path=relative_source_path,
        base_classes=sorted(
            f"{base.__module__}.{base.__qualname__}" for base in model.__bases__
        ),
        db_table=meta.db_table,
        abstract=meta.abstract,
        proxy=meta.proxy,
        managed=meta.managed,
        json_fields=sorted(
            field.name
            for field in meta.local_fields
            if isinstance(field, models.JSONField)
        ),
        custom_clean="clean" in model.__dict__,
        custom_save="save" in model.__dict__,
        target=ModelTarget.UNCLASSIFIED,
        owner="endoreg_db maintainers",
        rationale=(
            "Initial Django registry inventory; boundary and contract ownership "
            "review is pending."
        ),
        module_lx_dtypes_imports=list(lx_dtypes_imports),
        module_endoreg_schema_imports=list(endoreg_schema_imports),
    )


def discover_registered_models() -> tuple[ModelInventoryEntry, ...]:
    if not apps.ready:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", DEFAULT_SETTINGS_MODULE)
        django.setup()

    discovered = (
        _entry_from_model(model, kind=ModelKind.REGISTERED)
        for model in apps.get_models(include_auto_created=False)
        if model.__module__.startswith("endoreg_db.models.")
    )
    return tuple(sorted(discovered, key=lambda item: item.label))


def declares_abstract_meta(class_node: ast.ClassDef) -> bool:
    for node in class_node.body:
        if not isinstance(node, ast.ClassDef) or node.name != "Meta":
            continue
        for meta_node in node.body:
            if not isinstance(meta_node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = (
                meta_node.targets
                if isinstance(meta_node, ast.Assign)
                else [meta_node.target]
            )
            if not any(
                isinstance(target, ast.Name) and target.id == "abstract"
                for target in targets
            ):
                continue
            if isinstance(meta_node.value, ast.Constant):
                return meta_node.value.value is True
    return False


def _abstract_model_declarations() -> tuple[tuple[str, str], ...]:
    declarations: list[tuple[str, str]] = []
    models_root = PROJECT_ROOT / "endoreg_db" / "models"
    for source_path in sorted(models_root.rglob("*.py")):
        tree = ast.parse(
            source_path.read_text(encoding="utf-8"),
            filename=str(source_path),
        )
        relative_module = source_path.relative_to(PROJECT_ROOT).with_suffix("")
        module_name = ".".join(relative_module.parts)
        declarations.extend(
            (module_name, node.name)
            for node in tree.body
            if isinstance(node, ast.ClassDef) and declares_abstract_meta(node)
        )
    return tuple(declarations)


def discover_abstract_models() -> tuple[ModelInventoryEntry, ...]:
    if not apps.ready:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", DEFAULT_SETTINGS_MODULE)
        django.setup()

    discovered: list[ModelInventoryEntry] = []
    for module_name, class_name in _abstract_model_declarations():
        candidate = getattr(import_module(module_name), class_name, None)
        if (
            not isinstance(candidate, type)
            or not issubclass(candidate, models.Model)
            or not candidate._meta.abstract
        ):
            raise ValueError(
                f"Static abstract-model declaration is not a Django abstract "
                f"model: {module_name}.{class_name}"
            )
        discovered.append(
            _entry_from_model(candidate, kind=ModelKind.ABSTRACT)
        )
    return tuple(sorted(discovered, key=lambda item: item.label))


def discover_models() -> tuple[ModelInventoryEntry, ...]:
    discovered = (*discover_registered_models(), *discover_abstract_models())
    models_by_label = {item.label: item for item in discovered}
    if len(models_by_label) != len(discovered):
        raise ValueError("Model discovery produced duplicate labels")
    return tuple(models_by_label[label] for label in sorted(models_by_label))


def _runtime_shape(entry: ModelInventoryEntry) -> tuple[object, ...]:
    return (
        entry.kind,
        entry.python_path,
        entry.source_path,
        tuple(entry.base_classes),
        entry.db_table,
        entry.abstract,
        entry.proxy,
        entry.managed,
        tuple(entry.json_fields),
        entry.custom_clean,
        entry.custom_save,
        tuple(entry.module_lx_dtypes_imports),
        tuple(entry.module_endoreg_schema_imports),
    )


def compare_inventory(
    discovered: tuple[ModelInventoryEntry, ...],
    inventory: ModelInventory,
) -> ModelInventoryComparison:
    discovered_by_label = {item.label: item for item in discovered}
    inventory_by_label = {item.label: item for item in inventory.models}

    new_labels = sorted(discovered_by_label.keys() - inventory_by_label.keys())
    stale_labels = sorted(inventory_by_label.keys() - discovered_by_label.keys())
    shared_labels = sorted(discovered_by_label.keys() & inventory_by_label.keys())

    return ModelInventoryComparison(
        new_models=tuple(discovered_by_label[label] for label in new_labels),
        stale_models=tuple(inventory_by_label[label] for label in stale_labels),
        changed_models=tuple(
            (inventory_by_label[label], discovered_by_label[label])
            for label in shared_labels
            if _runtime_shape(inventory_by_label[label])
            != _runtime_shape(discovered_by_label[label])
        ),
    )


def _print_comparison(comparison: ModelInventoryComparison) -> None:
    for entry in comparison.new_models:
        print(f"NEW {entry.label}: {entry.python_path}")
    for entry in comparison.stale_models:
        print(f"STALE {entry.label}: {entry.python_path}")
    for expected, actual in comparison.changed_models:
        print(
            f"CHANGED {expected.label}: "
            f"expected={_runtime_shape(expected)!r} "
            f"actual={_runtime_shape(actual)!r}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compare registered endoreg_db Django models with the reviewed "
            "lx-dtypes standardization inventory."
        )
    )
    parser.add_argument(
        "--inventory",
        type=Path,
        default=DEFAULT_INVENTORY,
        help="Path to the reviewed model inventory YAML.",
    )
    args = parser.parse_args()

    inventory = load_inventory(args.inventory.resolve())
    discovered = discover_models()
    comparison = compare_inventory(discovered, inventory)
    _print_comparison(comparison)
    if not comparison.is_clean:
        return 1

    target_counts = {
        target: sum(item.target is target for item in inventory.models)
        for target in ModelTarget
    }
    counts = ", ".join(
        f"{target.value}={count}"
        for target, count in target_counts.items()
        if count
    )
    registered_count = sum(
        item.kind is ModelKind.REGISTERED for item in discovered
    )
    abstract_count = sum(item.kind is ModelKind.ABSTRACT for item in discovered)
    print(
        f"Model inventory clean: {registered_count} registered and "
        f"{abstract_count} abstract models; {counts}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

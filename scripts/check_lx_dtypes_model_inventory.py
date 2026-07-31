from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from functools import lru_cache
from importlib import import_module
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import cast, Iterable, Literal, Protocol

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
    boundary_consumers: list[str] = Field(default_factory=list)
    dynamic_model_references: list[str] = Field(default_factory=list)
    module_lx_dtypes_imports: list[str] = Field(default_factory=list)
    module_endoreg_schema_imports: list[str] = Field(default_factory=list)
    exception_owner: str | None = None
    exception_review_by: date | None = None
    exception_exit_criteria: str | None = None

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
        if self.boundary_consumers != sorted(set(self.boundary_consumers)):
            raise ValueError(
                f"{self.label} boundary_consumers must be sorted and unique"
            )
        if self.dynamic_model_references != sorted(set(self.dynamic_model_references)):
            raise ValueError(
                f"{self.label} dynamic_model_references must be sorted and unique"
            )
        if self.module_lx_dtypes_imports != sorted(set(self.module_lx_dtypes_imports)):
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
        if self.target is not ModelTarget.UNCLASSIFIED and not self.ownership_evidence:
            raise ValueError(
                f"{self.label} classified target requires ownership_evidence"
            )
        exception_values = (
            self.exception_owner,
            self.exception_review_by,
            self.exception_exit_criteria,
        )
        if self.target is ModelTarget.TEMPORARY_EXCEPTION:
            if not all(exception_values):
                raise ValueError(
                    f"{self.label} temporary exception requires owner, "
                    "review date, and exit criteria"
                )
        elif any(value is not None for value in exception_values):
            raise ValueError(
                f"{self.label} exception metadata requires temporary_exception target"
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
        elif name == "endoreg_db.schemas" or name.startswith("endoreg_db.schemas."):
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
    lx_dtypes_imports, endoreg_schema_imports = _source_contract_imports(source_path)

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


def _import_all_model_modules() -> None:
    models_root = PROJECT_ROOT / "endoreg_db" / "models"
    for source_path in sorted(models_root.rglob("*.py")):
        if source_path.name == "__init__.py":
            continue
        relative_module = source_path.relative_to(PROJECT_ROOT).with_suffix("")
        import_module(".".join(relative_module.parts))


def discover_registered_models() -> tuple[ModelInventoryEntry, ...]:
    if not apps.ready:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", DEFAULT_SETTINGS_MODULE)
        django.setup()
    _import_all_model_modules()

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
        discovered.append(_entry_from_model(candidate, kind=ModelKind.ABSTRACT))
    return tuple(sorted(discovered, key=lambda item: item.label))


def _boundary_kind(relative_path: Path) -> str:
    parts = relative_path.parts
    path_text = relative_path.as_posix().lower()
    if "serializers" in parts or "views" in parts:
        return "api"
    if "import_files" in parts or "import" in relative_path.stem.lower():
        return "import"
    if "management" in parts or "tasks" in parts or "job" in path_text:
        return "job"
    if "export" in path_text:
        return "export"
    if "schemas" in parts:
        return "schema"
    return "service"


def _boundary_source_files(source_root: Path) -> Iterable[Path]:
    for path in sorted(source_root.rglob("*")):
        if not path.is_file():
            continue
        relative_path = path.relative_to(source_root)
        if relative_path.parts[0] in {"migrations", "models"}:
            continue
        if path.suffix in {".py", ".json", ".yaml", ".yml"}:
            yield path


def discover_boundary_references(
    entries: tuple[ModelInventoryEntry, ...],
    *,
    source_root: Path = PROJECT_ROOT / "endoreg_db",
) -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    """Find stable boundary consumers and exact dynamic model references."""
    class_names = {entry.label.rsplit(".", 1)[-1] for entry in entries}
    labels_by_class = {entry.label.rsplit(".", 1)[-1]: entry.label for entry in entries}
    consumers: dict[str, set[str]] = {entry.label: set() for entry in entries}
    dynamic: dict[str, set[str]] = {entry.label: set() for entry in entries}

    for path in _boundary_source_files(source_root):
        within_source = path.relative_to(source_root)
        relative_path = (Path(source_root.name) / within_source).as_posix()
        kind = _boundary_kind(within_source)
        text = path.read_text(encoding="utf-8")
        identifiers: set[str] = set()
        string_values: set[str] = set()
        if path.suffix == ".py":
            tree = ast.parse(text, filename=str(path))
            identifiers.update(
                node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
            )
            identifiers.update(
                node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
            )
            string_values.update(
                node.value
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
            )
        else:
            identifiers.update(
                name
                for name in class_names
                if re.search(rf"\b{re.escape(name)}\b", text)
            )

        for class_name in class_names & identifiers:
            label = labels_by_class[class_name]
            consumers[label].add(f"{kind}:{relative_path}")
        for class_name, label in labels_by_class.items():
            accepted_references = {class_name, label}
            if string_values & accepted_references:
                dynamic[label].add(relative_path)

    return {
        entry.label: (
            tuple(sorted(consumers[entry.label])),
            tuple(sorted(dynamic[entry.label])),
        )
        for entry in entries
    }


def discover_models() -> tuple[ModelInventoryEntry, ...]:
    discovered = (*discover_registered_models(), *discover_abstract_models())
    models_by_label = {item.label: item for item in discovered}
    if len(models_by_label) != len(discovered):
        raise ValueError("Model discovery produced duplicate labels")
    ordered = tuple(models_by_label[label] for label in sorted(models_by_label))
    references = discover_boundary_references(ordered)
    return tuple(
        entry.model_copy(
            update={
                "boundary_consumers": list(references[entry.label][0]),
                "dynamic_model_references": list(references[entry.label][1]),
            }
        )
        for entry in ordered
    )


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
        tuple(entry.boundary_consumers),
        tuple(entry.dynamic_model_references),
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


def _write_inventory(path: Path, inventory: ModelInventory) -> None:
    payload = yaml.safe_dump(
        inventory.model_dump(mode="json"),
        allow_unicode=True,
        sort_keys=False,
        width=100,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        temporary_path = Path(handle.name)
        handle.write(payload)
    os.replace(temporary_path, path)


def refresh_boundary_metadata(
    *,
    path: Path,
    inventory: ModelInventory,
    discovered: tuple[ModelInventoryEntry, ...],
) -> None:
    discovered_by_label = {item.label: item for item in discovered}
    refreshed = inventory.model_copy(
        update={
            "models": [
                entry.model_copy(
                    update={
                        "boundary_consumers": discovered_by_label[
                            entry.label
                        ].boundary_consumers,
                        "dynamic_model_references": discovered_by_label[
                            entry.label
                        ].dynamic_model_references,
                    }
                )
                for entry in inventory.models
            ]
        }
    )
    _write_inventory(path, refreshed)


def refresh_runtime_metadata(
    *,
    path: Path,
    inventory: ModelInventory,
    discovered: tuple[ModelInventoryEntry, ...],
) -> None:
    """Refresh reproducible model metadata without changing review decisions."""

    discovered_by_label = {item.label: item for item in discovered}
    inventory_labels = {item.label for item in inventory.models}
    if inventory_labels != set(discovered_by_label):
        raise ValueError(
            "Cannot refresh runtime metadata while models are new or stale; "
            "review their classifications first"
        )
    runtime_fields = {
        "kind",
        "python_path",
        "source_path",
        "base_classes",
        "db_table",
        "abstract",
        "proxy",
        "managed",
        "json_fields",
        "custom_clean",
        "custom_save",
        "boundary_consumers",
        "dynamic_model_references",
        "module_lx_dtypes_imports",
        "module_endoreg_schema_imports",
    }
    refreshed = inventory.model_copy(
        update={
            "models": [
                entry.model_copy(
                    update=discovered_by_label[entry.label].model_dump(
                        include=runtime_fields
                    )
                )
                for entry in inventory.models
            ]
        }
    )
    _write_inventory(path, refreshed)


def timebox_unclassified_models(
    *,
    path: Path,
    inventory: ModelInventory,
    review_by: date,
) -> None:
    timeboxed = inventory.model_copy(
        update={
            "models": [
                entry.model_copy(
                    update={
                        "target": ModelTarget.TEMPORARY_EXCEPTION,
                        "owner": "endoreg_db and lx_dtypes maintainers",
                        "rationale": (
                            "Boundary inventory is complete, but canonical "
                            "contract ownership remains pending its risk-ordered "
                            "domain-cohort review."
                        ),
                        "ownership_evidence": sorted(
                            {
                                f"{entry.source_path}:{entry.label.rsplit('.', 1)[-1]}",
                                (
                                    "scripts/check_lx_dtypes_model_inventory.py:"
                                    "discover_boundary_references"
                                ),
                            }
                        ),
                        "exception_owner": "endoreg_db and lx_dtypes maintainers",
                        "exception_review_by": review_by,
                        "exception_exit_criteria": (
                            "The owning domain cohort reviews every discovered "
                            "consumer and dynamic reference, selects "
                            "shared_lx_dtypes_contract, local_boundary_schema, "
                            "or persistence_only, and records stable contract evidence."
                        ),
                    }
                )
                if entry.target is ModelTarget.UNCLASSIFIED
                else entry
                for entry in inventory.models
            ]
        }
    )
    payload = yaml.safe_dump(
        timeboxed.model_dump(mode="json"),
        allow_unicode=True,
        sort_keys=False,
        width=100,
    )
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        temporary_path = Path(handle.name)
        handle.write(payload)
    os.replace(temporary_path, path)


def classification_errors(
    inventory: ModelInventory,
    *,
    today: date | None = None,
) -> tuple[str, ...]:
    effective_today = today or date.today()
    errors: list[str] = []
    for entry in inventory.models:
        if entry.target is ModelTarget.UNCLASSIFIED:
            errors.append(f"{entry.label} remains unclassified")
        if (
            entry.target is ModelTarget.TEMPORARY_EXCEPTION
            and entry.exception_review_by is not None
            and entry.exception_review_by < effective_today
        ):
            errors.append(
                f"{entry.label} temporary exception expired on "
                f"{entry.exception_review_by.isoformat()}"
            )
    return tuple(errors)


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
    parser.add_argument(
        "--refresh-boundaries",
        action="store_true",
        help="Atomically refresh discovered consumer and dynamic-reference metadata.",
    )
    parser.add_argument(
        "--refresh-runtime-metadata",
        action="store_true",
        help=(
            "Atomically refresh all reproducible model structure, import, and "
            "boundary metadata while preserving reviewed classifications."
        ),
    )
    parser.add_argument(
        "--timebox-unclassified-until",
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help=(
            "Convert reviewed unclassified entries to explicit temporary "
            "exceptions with this review deadline."
        ),
    )
    args = parser.parse_args()

    inventory = load_inventory(args.inventory.resolve())
    discovered = discover_models()
    if args.refresh_runtime_metadata:
        refresh_runtime_metadata(
            path=args.inventory.resolve(),
            inventory=inventory,
            discovered=discovered,
        )
        inventory = load_inventory(args.inventory.resolve())
    if args.refresh_boundaries:
        refresh_boundary_metadata(
            path=args.inventory.resolve(),
            inventory=inventory,
            discovered=discovered,
        )
        inventory = load_inventory(args.inventory.resolve())
    if args.timebox_unclassified_until is not None:
        timebox_unclassified_models(
            path=args.inventory.resolve(),
            inventory=inventory,
            review_by=args.timebox_unclassified_until,
        )
        inventory = load_inventory(args.inventory.resolve())
    comparison = compare_inventory(discovered, inventory)
    _print_comparison(comparison)
    if not comparison.is_clean:
        return 1
    errors = classification_errors(inventory)
    if errors:
        for error in errors:
            print(f"CLASSIFICATION {error}")
        return 1

    target_counts = {
        target: sum(item.target is target for item in inventory.models)
        for target in ModelTarget
    }
    counts = ", ".join(
        f"{target.value}={count}" for target, count in target_counts.items() if count
    )
    registered_count = sum(item.kind is ModelKind.REGISTERED for item in discovered)
    abstract_count = sum(item.kind is ModelKind.ABSTRACT for item in discovered)
    print(
        f"Model inventory clean: {registered_count} registered and "
        f"{abstract_count} abstract models; {counts}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

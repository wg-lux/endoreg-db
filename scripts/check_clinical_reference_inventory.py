"""Verify the reviewed clinical graph fields and complete bootstrap ownership map."""

import ast
from pathlib import Path
from typing import Literal, get_args

from lx_dtypes.models.contracts.core_concepts import CoreConceptCollection
from lx_dtypes.models.contracts.reference_catalog import CATALOG_RECORD_TYPES
from pydantic import BaseModel, ConfigDict, Field
import yaml


class CollectionInventory(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    contract: str
    fields: list[str]
    persistence: Literal["validated_immutable_snapshot"]
    relational_models: list[str]


class CommandInventory(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    owner: Literal[
        "clinical_graph", "clinical_catalog", "host_operations", "study_preset"
    ]
    purpose: str = Field(min_length=1)
    record_types: list[str]


class ClinicalInventory(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["1.0"]
    feature_id: Literal["dtypes_study_definitions"]
    collections: dict[str, CollectionInventory]
    bootstrap_commands: dict[str, CommandInventory]
    catalog_fields: dict[str, list[str]]


def current_collections() -> dict[str, tuple[str, list[str]]]:
    result: dict[str, tuple[str, list[str]]] = {}
    for name, field in CoreConceptCollection.model_fields.items():
        args = get_args(field.annotation)
        if not args:
            continue
        model = args[0]
        if isinstance(model, type) and issubclass(model, BaseModel):
            result[name] = (
                f"{model.__module__}.{model.__name__}",
                sorted(model.model_fields),
            )
    return result


def bootstrap_commands(source: str) -> set[str]:
    return {
        node.args[0].value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "call_command"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }


def validate_inventory(inventory: ClinicalInventory, bootstrap_source: str) -> None:
    expected = current_collections()
    if set(inventory.collections) != set(expected):
        raise ValueError(
            "Clinical graph collection inventory differs from the public schema"
        )
    for name, (contract, fields) in expected.items():
        entry = inventory.collections[name]
        if entry.contract != contract or entry.fields != fields:
            raise ValueError(f"Clinical field inventory needs review: {name}")
    if (
        bootstrap_commands(bootstrap_source)
        or "ReferenceCatalogCommand" not in bootstrap_source
    ):
        raise ValueError("Bootstrap command ownership inventory needs review")
    expected_catalog = {
        kind.value: sorted(contract.model_fields)
        for kind, contract in CATALOG_RECORD_TYPES.items()
    }
    if inventory.catalog_fields != expected_catalog:
        raise ValueError("Reference catalogue field inventory needs review")


def validate_compatibility_commands(inventory: ClinicalInventory, root: Path) -> None:
    for name, entry in inventory.bootstrap_commands.items():
        source = (root / "endoreg_db/management/commands" / f"{name}.py").read_text()
        if (
            "ReferenceCatalogCommand" not in source
            or "load_model_data_from_yaml" in source
        ):
            raise ValueError(f"Legacy command has not migrated: {name}")
        selected = [
            node.attr.lower()
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "ReferenceKind"
        ]
        if selected != entry.record_types:
            raise ValueError(f"Reference catalogue selection changed: {name}")


def validate_legacy_retirement(root: Path) -> None:
    retired = {"dataloader", "yaml_model_loader"}
    for name in retired:
        for suffix in (".py", ".pyi"):
            if (root / "endoreg_db/utils" / f"{name}{suffix}").exists():
                raise ValueError(f"Retired loader restored: {name}{suffix}")
    for path in (root / "endoreg_db").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [f"{node.module}.{alias.name}" for alias in node.names]
                names.append(node.module or "")
            else:
                continue
            if any(
                name == f"endoreg_db.utils.{retired_name}"
                or name.startswith(f"endoreg_db.utils.{retired_name}.")
                for name in names
                for retired_name in retired
            ):
                raise ValueError(f"Retired loader consumer: {path}")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    inventory = ClinicalInventory.model_validate(
        yaml.safe_load(
            (root / "quality/clinical_reference_projection.yml").read_text(
                encoding="utf-8"
            )
        )
    )
    validate_inventory(
        inventory,
        (root / "endoreg_db/management/commands/load_base_db_data.py").read_text(
            encoding="utf-8"
        ),
    )
    validate_compatibility_commands(inventory, root)
    validate_legacy_retirement(root)
    print(
        f"Clinical inventory valid: {len(inventory.collections)} collections, {sum(len(c.fields) for c in inventory.collections.values())} fields, {len(inventory.bootstrap_commands)} bootstrap commands"
    )


if __name__ == "__main__":
    main()

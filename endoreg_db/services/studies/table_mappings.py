"""User-owned immutable mapping documents in canonical protected runtime storage."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import yaml
from lx_dtypes.models.contracts.table_import import TableMapping

from endoreg_db.services.studies.table_parser import (
    load_table_mapping,
    parse_table_mapping,
)
from endoreg_db.utils.file_operations import atomic_create_file, ensure_directory
from endoreg_db.utils.paths import ensure_within_storage_root, get_runtime_paths


def mapping_directory(owner_id: int) -> Path:
    if owner_id < 1:
        raise ValueError("A persisted mapping owner is required")
    root = get_runtime_paths().table_mappings
    candidate = ensure_within_storage_root(root / str(owner_id))
    if not candidate.is_relative_to(root.resolve()):
        raise ValueError("Mapping directory escapes its root")
    return candidate


def mapping_path(owner_id: int, mapping_id: UUID) -> Path:
    root = mapping_directory(owner_id)
    path = ensure_within_storage_root(root / f"{mapping_id}.yml")
    if path.parent != root:
        raise ValueError("Mapping file escapes its owner directory")
    return path


def save_table_mapping(owner_id: int, raw: str) -> TableMapping:
    spec = parse_table_mapping(raw)
    destination = mapping_path(owner_id, spec.mapping_id)
    ensure_directory(destination.parent, dir_mode=0o700)
    content = yaml.safe_dump(spec.model_dump(mode="json"), sort_keys=False).encode(
        "utf-8"
    )
    try:
        atomic_create_file(
            destination=destination, content=[content], file_mode=0o600, dir_mode=0o700
        )
    except FileExistsError:
        if load_table_mapping(destination) != spec:
            raise FileExistsError(
                "Mapping ID already exists with different content"
            ) from None
    return spec


def list_table_mappings(owner_id: int) -> list[TableMapping]:
    root = mapping_directory(owner_id)
    if not root.exists():
        return []
    return [
        load_table_mapping(mapping_path(owner_id, UUID(path.stem)))
        for path in sorted(root.glob("*.yml"))
    ]

"""Canonical YAML model loader module.

This module provides the preferred import path for YAML-to-ORM loading logic.
The implementation currently lives in ``endoreg_db.utils.dataloader`` to keep
backward compatibility with existing imports.
"""

from __future__ import annotations

from endoreg_db.helpers.typing import (
    LoadModelDataDirectory,
    LoadModelDataMetadata,
    LoadModelDataModel,
    LoadModelDataValidator,
    YamlEntry,
    YamlScalar,
    YamlValue,
)
from endoreg_db.utils.dataloader import (
    load_data_with_foreign_keys,
    load_model_data_from_yaml,
)

__all__ = [
    "LoadModelDataDirectory",
    "LoadModelDataMetadata",
    "LoadModelDataModel",
    "LoadModelDataValidator",
    "YamlEntry",
    "YamlScalar",
    "YamlValue",
    "load_data_with_foreign_keys",
    "load_model_data_from_yaml",
]

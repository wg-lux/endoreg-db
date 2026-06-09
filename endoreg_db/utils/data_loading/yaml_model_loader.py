"""Canonical YAML model loader module.

This module provides the preferred import path for YAML-to-ORM loading logic.
The implementation currently lives in ``endoreg_db.utils.data_loading.dataloader`` to keep
backward compatibility with existing imports.
"""

from endoreg_db.utils.data_loading.dataloader import (
    load_data_with_foreign_keys,
    load_model_data_from_yaml,
)

__all__ = [
    "load_model_data_from_yaml",
    "load_data_with_foreign_keys",
]

"""Canonical YAML model loader module.

This module provides the preferred import path for YAML-to-ORM loading logic.
The implementation currently lives in ``endoreg_db.utils.dataloader`` to keep
backward compatibility with existing imports.
"""

from __future__ import annotations

from pathlib import Path
from types import NoneType
from typing import TYPE_CHECKING, NotRequired, Protocol, TypeAlias, TypedDict, cast

from django.core.management.base import BaseCommand
from django.db import models

from endoreg_db.utils.dataloader import (
    load_data_with_foreign_keys,
    load_model_data_from_yaml as _load_model_data_from_yaml,
)

if TYPE_CHECKING:
    from endoreg_db.utils.dataloader import (
        LoadModelDataMetadata as DataLoaderLoadModelDataMetadata,
    )

LoadModelDataModel: TypeAlias = type[models.Model]
LoadModelDataDirectory: TypeAlias = str | Path
YamlNull: TypeAlias = NoneType
YamlScalar: TypeAlias = str | int | float | bool | YamlNull
YamlValue: TypeAlias = YamlScalar | list[YamlScalar]


class YamlEntry(TypedDict):
    fields: dict[str, YamlValue]


class LoadModelDataValidator(Protocol):
    def __call__(
        self,
        fields: dict[str, YamlValue],
        *,
        entry: YamlEntry,
        model: LoadModelDataModel,
    ) -> None: ...


class LoadModelDataMetadata(TypedDict):
    dir: LoadModelDataDirectory
    model: LoadModelDataModel
    foreign_keys: list[str]
    foreign_key_models: list[LoadModelDataModel]
    validators: NotRequired[list[LoadModelDataValidator]]


def load_model_data_from_yaml(
    command: BaseCommand,
    model_name: str,
    metadata: LoadModelDataMetadata,
    verbose: bool = False,
) -> None:
    _load_model_data_from_yaml(
        command,
        model_name,
        cast("DataLoaderLoadModelDataMetadata", metadata),
        verbose,
    )


__all__ = [
    "LoadModelDataDirectory",
    "LoadModelDataMetadata",
    "LoadModelDataModel",
    "LoadModelDataValidator",
    "YamlEntry",
    "YamlNull",
    "YamlScalar",
    "YamlValue",
    "load_data_with_foreign_keys",
    "load_model_data_from_yaml",
]

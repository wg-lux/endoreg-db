from __future__ import annotations

from pathlib import Path
from typing import NotRequired, Protocol, TypeAlias, TypedDict

from django.core.management.base import BaseCommand
from django.db import models

YamlNull: TypeAlias = None
YamlScalar: TypeAlias = str | int | float | bool | YamlNull
YamlValue: TypeAlias = YamlScalar | list[YamlScalar]
DataLoaderModel: TypeAlias = type[models.Model]

class YamlEntry(TypedDict):
    fields: dict[str, YamlValue]

class DataLoaderValidator(Protocol):
    def __call__(
        self,
        fields: dict[str, YamlValue],
        *,
        entry: YamlEntry,
        model: DataLoaderModel,
    ) -> None: ...

class LoadModelDataMetadata(TypedDict):
    dir: str | Path
    model: DataLoaderModel
    foreign_keys: list[str]
    foreign_key_models: list[DataLoaderModel]
    validators: NotRequired[list[DataLoaderValidator]]

def load_model_data_from_yaml(
    command: BaseCommand,
    model_name: str,
    metadata: LoadModelDataMetadata,
    verbose: bool = False,
) -> None: ...
def load_data_with_foreign_keys(
    command: BaseCommand,
    model: DataLoaderModel,
    yaml_data: list[YamlEntry],
    foreign_keys: list[str],
    foreign_key_models: list[DataLoaderModel],
    validators: list[DataLoaderValidator],
    verbose: bool,
    log_context: str | YamlNull = None,
) -> None: ...

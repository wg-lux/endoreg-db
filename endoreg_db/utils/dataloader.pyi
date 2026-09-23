from __future__ import annotations

from django.core.management.base import BaseCommand

from endoreg_db.helpers.typing import (
    LoadModelDataMetadata as LoadModelDataMetadata,
    LoadModelDataModel,
    LoadModelDataValidator,
    YamlEntry as YamlEntry,
    YamlScalar as YamlScalar,
    YamlValue as YamlValue,
)

class DataLoaderSourceError(ValueError): ...

def load_model_data_from_yaml(
    command: BaseCommand,
    model_name: str,
    metadata: LoadModelDataMetadata,
    verbose: bool = False,
) -> None: ...
def load_data_with_foreign_keys(
    command: BaseCommand,
    model: LoadModelDataModel,
    yaml_data: list[YamlEntry],
    foreign_keys: list[str],
    foreign_key_models: list[LoadModelDataModel],
    validators: list[LoadModelDataValidator],
    verbose: bool,
    log_context: str | None = None,
) -> None: ...

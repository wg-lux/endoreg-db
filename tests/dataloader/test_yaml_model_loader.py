from __future__ import annotations

import pytest

from endoreg_db.helpers import typing as model_data
from endoreg_db.utils import dataloader, yaml_model_loader


@pytest.mark.parametrize(
    "name",
    [
        "LoadModelDataMetadata",
        "LoadModelDataValidator",
        "YamlEntry",
        "YamlScalar",
        "YamlValue",
    ],
)
def test_loader_facades_share_canonical_contracts(name: str) -> None:
    canonical = getattr(model_data, name)
    assert getattr(dataloader, name) is canonical
    assert getattr(yaml_model_loader, name) is canonical


def test_yaml_loader_facade_exports_original_workflow() -> None:
    assert (
        yaml_model_loader.load_model_data_from_yaml
        is dataloader.load_model_data_from_yaml
    )
    assert (
        yaml_model_loader.load_data_with_foreign_keys
        is dataloader.load_data_with_foreign_keys
    )

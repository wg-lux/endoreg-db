from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import models
from pytest import MonkeyPatch

import endoreg_db.utils.yaml_model_loader as yaml_model_loader


def test_load_model_data_from_yaml_delegates_typed_metadata_unchanged(
    monkeypatch: MonkeyPatch,
) -> None:
    command = BaseCommand()
    metadata: yaml_model_loader.LoadModelDataMetadata = {
        "dir": Path("fixtures/genders"),
        "model": models.Model,
        "foreign_keys": [],
        "foreign_key_models": [],
    }
    calls: list[tuple[BaseCommand, str, object, bool]] = []

    def record_delegate(
        command_arg: BaseCommand,
        model_name_arg: str,
        metadata_arg: object,
        verbose_arg: bool = False,
    ) -> None:
        calls.append((command_arg, model_name_arg, metadata_arg, verbose_arg))

    monkeypatch.setattr(
        yaml_model_loader,
        "_load_model_data_from_yaml",
        record_delegate,
    )

    yaml_model_loader.load_model_data_from_yaml(
        command,
        "gender",
        metadata,
        verbose=True,
    )

    assert calls == [(command, "gender", metadata, True)]

from pathlib import Path
from typing import Any, cast

from pytest import MonkeyPatch
from django.core.management.base import BaseCommand

from endoreg_db.models import InformationSourceType
from endoreg_db.utils import dataloader


def _dataloader_connection() -> Any:
    return cast(Any, dataloader).connection


def _recorded_warning_sink(
    recorded: list[tuple[str, str]],
):
    def record_warning(
        command: BaseCommand,
        message: str,
        verbose: bool,
        context: str,
    ) -> None:
        _ = command
        _ = verbose
        recorded.append((context, message))

    return record_warning


def test_load_model_data_from_yaml_skips_when_model_table_is_missing(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    recorded: list[tuple[str, str]] = []

    def no_tables() -> list[str]:
        return []

    monkeypatch.setattr(
        _dataloader_connection().introspection, "table_names", no_tables
    )
    monkeypatch.setattr(
        dataloader,
        "_record_warning",
        _recorded_warning_sink(recorded),
    )

    dataloader.load_model_data_from_yaml(
        BaseCommand(),
        InformationSourceType.__name__,
        {
            "dir": tmp_path,
            "model": InformationSourceType,
            "foreign_keys": [],
            "foreign_key_models": [],
        },
        verbose=False,
    )

    assert recorded == [
        (
            InformationSourceType.__name__,
            "Skipping load because database tables are not available yet: "
            f"{InformationSourceType._meta.db_table}",
        )
    ]


def test_load_model_data_from_yaml_skips_when_yaml_directory_is_missing(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    recorded: list[tuple[str, str]] = []
    missing_dir = tmp_path / "missing"

    monkeypatch.setattr(
        _dataloader_connection().introspection,
        "table_names",
        lambda: [str(InformationSourceType._meta.db_table)],
    )
    monkeypatch.setattr(
        dataloader,
        "_record_warning",
        _recorded_warning_sink(recorded),
    )

    dataloader.load_model_data_from_yaml(
        BaseCommand(),
        InformationSourceType.__name__,
        {
            "dir": missing_dir,
            "model": InformationSourceType,
            "foreign_keys": [],
            "foreign_key_models": [],
        },
        verbose=False,
    )

    assert recorded == [
        (
            InformationSourceType.__name__,
            f"Skipping load because YAML data directory is missing: {missing_dir}",
        )
    ]

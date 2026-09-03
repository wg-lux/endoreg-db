from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from django.core.management.base import BaseCommand
import pytest

from endoreg_db.models import InformationSourceType
from endoreg_db.utils import dataloader


def _dataloader_module() -> Any:
    return cast(Any, dataloader)


def _enable_loader(monkeypatch: pytest.MonkeyPatch, base_data_root: Path) -> None:
    monkeypatch.setattr(dataloader, "_BASE_DATA_ROOT", base_data_root)
    monkeypatch.setattr(
        _dataloader_module().connection.introspection,
        "table_names",
        lambda: [str(InformationSourceType._meta.db_table)],
    )


def _metadata(source_directory: Path) -> dataloader.LoadModelDataMetadata:
    return {
        "dir": source_directory,
        "model": cast(Any, InformationSourceType),
        "foreign_keys": [],
        "foreign_key_models": [],
        "validators": [],
    }


def test_loader_rejects_directory_outside_packaged_base_data_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    approved_root = tmp_path / "approved"
    approved_root.mkdir()
    outside_root = tmp_path / "outside"
    outside_root.mkdir()
    outside_root.joinpath("data.yaml").write_text("[]", encoding="utf-8")
    _enable_loader(monkeypatch, approved_root)
    load_calls: list[object] = []

    def record_load_call(*args: object, **kwargs: object) -> None:
        load_calls.append((args, kwargs))

    monkeypatch.setattr(
        dataloader,
        "load_data_with_foreign_keys",
        record_load_call,
    )

    with pytest.raises(
        dataloader.DataLoaderSourceError,
        match="within the packaged base-data root",
    ):
        dataloader.load_model_data_from_yaml(
            BaseCommand(),
            InformationSourceType.__name__,
            _metadata(outside_root),
        )

    assert load_calls == []


def test_loader_rejects_symlinked_yaml_file_before_reading(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    approved_root = tmp_path / "approved"
    source_directory = approved_root / "source"
    source_directory.mkdir(parents=True)
    target = approved_root / "target.yaml"
    target.write_text("[]", encoding="utf-8")
    source_directory.joinpath("linked.yaml").symlink_to(target)
    _enable_loader(monkeypatch, approved_root)
    load_calls: list[object] = []

    def record_load_call(*args: object, **kwargs: object) -> None:
        load_calls.append((args, kwargs))

    monkeypatch.setattr(
        dataloader,
        "load_data_with_foreign_keys",
        record_load_call,
    )

    with pytest.raises(
        dataloader.DataLoaderSourceError,
        match="source files must not be symlinks",
    ):
        dataloader.load_model_data_from_yaml(
            BaseCommand(),
            InformationSourceType.__name__,
            _metadata(source_directory),
        )

    assert load_calls == []

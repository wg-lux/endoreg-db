from pathlib import Path

from django.core.management.base import BaseCommand

from endoreg_db.models import InformationSourceType
from endoreg_db.utils import dataloader


def test_load_model_data_from_yaml_skips_when_model_table_is_missing(
    monkeypatch, tmp_path: Path
):
    recorded: list[tuple[str, str]] = []

    monkeypatch.setattr(dataloader.connection.introspection, "table_names", lambda: [])
    monkeypatch.setattr(
        dataloader,
        "_record_warning",
        lambda command, message, verbose, context: recorded.append((context, message)),
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

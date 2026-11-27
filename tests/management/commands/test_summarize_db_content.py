# pyright: reportPrivateUsage=false
from collections.abc import Iterator
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.db import models

from endoreg_db.management.commands import summarize_db_content as command_module
from endoreg_db.models import Center


def _center_model_only(
    include_auto_created: bool = False,
    include_swapped: bool = False,
) -> Iterator[type[models.Model]]:
    del include_auto_created, include_swapped
    return iter((Center,))


@pytest.mark.django_db
def test_summarize_db_content_writes_non_empty_models(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Center.objects.create(name="summary-center", display_name="Summary Center")
    expected_center_count = Center.objects.count()
    app_config = command_module.apps.get_app_config("endoreg_db")
    monkeypatch.setattr(app_config, "path", str(tmp_path))
    monkeypatch.setattr(app_config, "get_models", _center_model_only)
    output = StringIO()

    call_command("summarize_db_content", stdout=output)

    csv_path = tmp_path / "data" / "db_summary.csv"
    assert (
        csv_path.read_bytes()
        == f"Model Name,Total Records\r\nCenter,{expected_center_count}\r\n".encode(
            "utf-8"
        )
    )
    assert (tmp_path / "data" / "db_summary.xlsx").is_file()
    assert "Database content summarization finished." in output.getvalue()


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@"])
def test_summary_csv_escapes_text(tmp_path: Path, prefix: str) -> None:
    output = tmp_path / "summary.csv"
    command_module._write_csv_safely(
        command_module.Command(), output, [(f"{prefix}test", 1)]
    )
    assert output.read_bytes() == f"'{prefix}test,1\r\n".encode("utf-8")

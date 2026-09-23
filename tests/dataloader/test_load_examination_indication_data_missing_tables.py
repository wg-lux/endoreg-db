# pyright: reportPrivateUsage=false
from __future__ import annotations

from collections.abc import Callable
from typing import NoReturn

import pytest

from endoreg_db.management.commands import load_examination_indication_data
from endoreg_db.management.commands.load_examination_indication_data import Command


def _empty_table_names() -> list[str]:
    return []


def _identity_warning(message: str) -> str:
    return message


def _raise_should_not_load(module_name: str) -> NoReturn:
    raise AssertionError(f"should not load {module_name}")


def test_load_from_dtypes_skips_when_required_tables_are_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = Command()
    writes: list[str] = []

    table_names = _empty_table_names
    load_dtypes_knowledge_base: Callable[[str], NoReturn] = _raise_should_not_load

    monkeypatch.setattr(
        load_examination_indication_data.connection.introspection,
        "table_names",
        table_names,
    )
    monkeypatch.setattr(
        load_examination_indication_data,
        "_load_dtypes_knowledge_base",
        load_dtypes_knowledge_base,
    )
    monkeypatch.setattr(command.stdout, "write", writes.append)
    monkeypatch.setattr(command.style, "WARNING", _identity_warning)

    command._load_from_dtypes(
        verbose=True,
        module_name="lx_examinations",
        strict=False,
    )

    assert writes == [
        "[dtypes] Skipping load because database tables are not available yet: "
        "endoreg_db_examination, endoreg_db_examinationindication, "
        "endoreg_db_examinationindicationclassification, "
        "endoreg_db_findingintervention, endoreg_db_informationsource"
    ]

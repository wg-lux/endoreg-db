"""Activation export boundaries; all generated data stays in memory."""

import csv
import io
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml
from openpyxl import Workbook

from endoreg_db.services.electrical.activation_import import (
    ActivationExportError,
    ActivationSourceFormat,
    parse_activation_export,
)
from endoreg_db.utils.hashs import get_bytes_hash

pytestmark = pytest.mark.no_db


@pytest.fixture
def csv_export() -> bytes:
    fixture = yaml.safe_load(
        Path(__file__).with_name("electrical_activation_export.yml").read_text()
    )
    return fixture["csv"].encode()


def workbook(content: bytes) -> bytes:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    for index, row in enumerate(csv.reader(io.StringIO(content.decode()))):
        cells: list[object] = list(row)
        if index:
            cells[1] = datetime.fromisoformat(row[1])
            for column in (0, 4, 5, 6, 7, 8):
                cells[column] = float(row[column])
        sheet.append(cells)
    buffer = io.BytesIO()
    book.save(buffer)
    book.close()
    return buffer.getvalue()


def change_member(content: bytes, name: str, old: bytes, new: bytes) -> bytes:
    buffer = io.BytesIO()
    with ZipFile(io.BytesIO(content)) as source, ZipFile(buffer, "w") as target:
        for member in source.infolist():
            data = source.read(member.filename)
            target.writestr(
                member, data.replace(old, new) if member.filename == name else data
            )
    return buffer.getvalue()


@pytest.mark.parametrize("kind", list(ActivationSourceFormat))
def test_start_timezone_summaries_and_zero_duration_are_preserved(
    csv_export: bytes, kind: ActivationSourceFormat
) -> None:
    content = csv_export if kind == ActivationSourceFormat.CSV else workbook(csv_export)
    parsed = parse_activation_export(
        content, source_format=kind, source_timezone="Etc/GMT-1"
    )
    assert parsed.source_sha256 == get_bytes_hash(content)
    assert len(parsed.log.activations) == 3
    first, zero, last = parsed.log.activations
    assert first.started_at == datetime(2026, 1, 1, 0, 0, 0, 123456, tzinfo=UTC)
    assert first.average_power_w == Decimal("1.25")
    assert first.energy_j == Decimal("0.125")
    assert zero.duration_s == 0 and zero.ended_at == zero.started_at
    assert last.energy_j == Decimal("0.5")  # Per activation; never accumulated.
    assert parsed.timestamp_rounding_error_seconds <= Decimal("0.0000005")


def test_xlsx_preserves_numeric_lexemes_without_float_rounding(
    csv_export: bytes,
) -> None:
    exact = "1.250000000000000000123456789"
    content = change_member(
        workbook(csv_export),
        "xl/worksheets/sheet1.xml",
        b"<v>1.25</v>",
        f"<v>{exact}</v>".encode(),
    )
    parsed = parse_activation_export(
        content, source_format=ActivationSourceFormat.XLSX, source_timezone="Etc/GMT-1"
    )
    assert parsed.log.activations[0].average_power_w == Decimal(exact)


@pytest.mark.parametrize("replacement", [b"<f>1+1</f><v>1.25</v>", b"<v>NaN</v>"])
def test_xlsx_rejects_formulas_and_nonfinite_measurements(
    csv_export: bytes, replacement: bytes
) -> None:
    content = change_member(
        workbook(csv_export), "xl/worksheets/sheet1.xml", b"<v>1.25</v>", replacement
    )
    with pytest.raises(ValueError):
        parse_activation_export(
            content,
            source_format=ActivationSourceFormat.XLSX,
            source_timezone="Etc/GMT-1",
        )


@pytest.mark.parametrize(
    "content", [b"", b"not a workbook", b"x" * (16 * 1024 * 1024 + 1)]
)
def test_rejects_invalid_or_oversized_workbook(content: bytes) -> None:
    with pytest.raises(ActivationExportError):
        parse_activation_export(
            content,
            source_format=ActivationSourceFormat.XLSX,
            source_timezone="Etc/GMT-1",
        )


def test_rejects_duplicate_archive_members(csv_export: bytes) -> None:
    buffer = io.BytesIO(workbook(csv_export))
    with ZipFile(buffer, "a") as archive, pytest.warns(UserWarning):
        archive.writestr("xl/workbook.xml", b"duplicate")
    with pytest.raises(ActivationExportError, match="duplicate"):
        parse_activation_export(
            buffer.getvalue(),
            source_format=ActivationSourceFormat.XLSX,
            source_timezone="Etc/GMT-1",
        )


@pytest.mark.parametrize(
    "replacement", [b"worksheets/absent.xml", b"https://invalid.example/sheet.xml"]
)
def test_rejects_mismatched_sheet_relationship(
    csv_export: bytes, replacement: bytes
) -> None:
    content = change_member(
        workbook(csv_export),
        "xl/_rels/workbook.xml.rels",
        b"/xl/worksheets/sheet1.xml",
        replacement,
    )
    with pytest.raises(ActivationExportError, match="Declared worksheet"):
        parse_activation_export(
            content,
            source_format=ActivationSourceFormat.XLSX,
            source_timezone="Etc/GMT-1",
        )

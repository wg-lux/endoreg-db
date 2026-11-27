"""Bounded activation export decoding; summaries never become sampled curves."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from pathlib import PurePosixPath
from xml.etree.ElementTree import Element, ParseError
from zipfile import BadZipFile, ZipFile

from defusedxml.ElementTree import fromstring
from defusedxml.common import DefusedXmlException
from lx_dtypes.models.contracts.electrosurgical_activations import (
    MAX_ACTIVATION_CSV_BYTES,
    MAX_ACTIVATIONS,
    ElectrosurgicalActivationLog,
    parse_electrosurgical_activation_csv,
)

from endoreg_db.utils.hashs import get_bytes_hash
from endoreg_db.schemas.electrosurgical_activation import ActivationSourceFormat

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_CELL = re.compile(r"([A-J])([1-9][0-9]*)$")


class ActivationExportError(ValueError):
    """The complete export was rejected; no partial import is permitted."""


@dataclass(frozen=True, slots=True)
class ParsedActivationExport:
    source_format: ActivationSourceFormat
    source_sha256: str
    log: ElectrosurgicalActivationLog
    # Excel stores fractional days. Preserve numeric cell lexemes and round only
    # datetime conversion to Python's microsecond resolution, with an exact bound.
    timestamp_rounding_error_seconds: Decimal


def parse_activation_export(
    content: bytes, *, source_format: ActivationSourceFormat, source_timezone: str
) -> ParsedActivationExport:
    if not content or len(content) > MAX_ACTIVATION_CSV_BYTES:
        raise ActivationExportError("Activation export is empty or exceeds 16 MiB")
    match source_format:
        case ActivationSourceFormat.CSV:
            normalized, error = content, Decimal(0)
        case ActivationSourceFormat.XLSX:
            normalized, error = _xlsx_csv(content)
    log = parse_electrosurgical_activation_csv(
        normalized, source_timezone=source_timezone
    )
    return ParsedActivationExport(source_format, get_bytes_hash(content), log, error)


def _xml(archive: ZipFile, name: str) -> Element:
    return fromstring(archive.read(name))


def _check_archive(archive: ZipFile) -> str:
    members = archive.infolist()
    names = [entry.filename for entry in members]
    if len(names) > 128 or len(set(names)) != len(names):
        raise ActivationExportError("Workbook has too many or duplicate members")
    if sum(entry.file_size for entry in members) > 64 * 1024 * 1024:
        raise ActivationExportError("Expanded workbook exceeds 64 MiB")
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name:
            raise ActivationExportError("Invalid workbook member path")
        if name.startswith("xl/externalLinks/"):
            raise ActivationExportError("External workbook references are unsupported")
    sheets = [
        name
        for name in names
        if name.startswith("xl/worksheets/") and name.endswith(".xml")
    ]
    if len(sheets) != 1:
        raise ActivationExportError("Activation export requires exactly one worksheet")
    return sheets[0]


def _cell_text(cell: Element, strings: list[str]) -> str:
    if cell.find(f"{_NS}f") is not None:
        raise ActivationExportError("Workbook formulas are not activation measurements")
    value = cell.findtext(f"{_NS}v", default="")
    match cell.get("t", "n"):
        case "s":
            index = int(value)
            if not 0 <= index < len(strings):
                raise ActivationExportError("Invalid shared-string index")
            return strings[index]
        case "inlineStr":
            return "".join(part.text or "" for part in cell.iter(f"{_NS}t"))
        case "n" | "d":
            return value
        case _:
            raise ActivationExportError("Unsupported activation cell type")


def _require_declared_sheet(
    archive: ZipFile, workbook: Element, sheet_name: str
) -> None:
    sheets = workbook.findall(f"{_NS}sheets/{_NS}sheet")
    if len(sheets) != 1:
        raise ActivationExportError("Activation export requires one declared worksheet")
    relationship_id = sheets[0].get(f"{{{_DOC_REL}}}id")
    relationships = _xml(archive, "xl/_rels/workbook.xml.rels").findall(
        f"{_REL_NS}Relationship"
    )
    matches = [item for item in relationships if item.get("Id") == relationship_id]
    if not relationship_id or len(matches) != 1:
        raise ActivationExportError("Worksheet relationship is missing or ambiguous")
    relationship = matches[0]
    target = relationship.get("Target", "")
    resolved = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
    if (
        relationship.get("TargetMode") == "External"
        or relationship.get("Type") != f"{_DOC_REL}/worksheet"
        or resolved != sheet_name
    ):
        raise ActivationExportError("Declared worksheet does not match the export")


def _excel_datetime(value: str, epoch: datetime) -> tuple[str, Decimal]:
    with localcontext() as context:
        context.prec = 50
        days = Decimal(value)
        if not days.is_finite() or not 61 <= days <= 2958465:
            raise ActivationExportError("Excel timestamp is outside supported bounds")
        exact_us = days * 86400 * 1_000_000
        rounded_us = exact_us.to_integral_value(rounding=ROUND_HALF_EVEN)
        converted = epoch + timedelta(microseconds=int(rounded_us))
        return converted.isoformat(sep=" "), abs(exact_us - rounded_us) / 1_000_000


def _worksheet_csv(
    sheet: Element, strings: list[str], epoch: datetime
) -> tuple[bytes, Decimal]:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    maximum_error = Decimal(0)
    rows = sheet.findall(f"{_NS}sheetData/{_NS}row")
    if not 1 < len(rows) <= MAX_ACTIVATIONS + 1:
        raise ActivationExportError("Workbook row count is outside supported bounds")
    for index, row in enumerate(rows):
        if row.get("r") != str(index + 1):
            raise ActivationExportError("Activation worksheet rows must be consecutive")
        values = [""] * 10
        seen: set[int] = set()
        for cell in row.findall(f"{_NS}c"):
            coordinate = _CELL.fullmatch(cell.get("r", ""))
            if coordinate is None or coordinate[2] != row.get("r"):
                raise ActivationExportError("Invalid activation cell coordinate")
            column = ord(coordinate[1]) - ord("A")
            if column in seen:
                raise ActivationExportError("Duplicate activation cell coordinate")
            seen.add(column)
            value = _cell_text(cell, strings)
            if index > 0 and column == 1 and cell.get("t", "n") == "n":
                value, error = _excel_datetime(value, epoch)
                maximum_error = max(maximum_error, error)
            values[column] = value
        writer.writerow(values)
    return output.getvalue().encode("utf-8"), maximum_error


def _xlsx_csv(content: bytes) -> tuple[bytes, Decimal]:
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            sheet_name = _check_archive(archive)
            workbook = _xml(archive, "xl/workbook.xml")
            _require_declared_sheet(archive, workbook, sheet_name)
            properties = workbook.find(f"{_NS}workbookPr")
            date1904 = properties is not None and properties.get("date1904") in {
                "1",
                "true",
            }
            epoch = datetime(1904, 1, 1) if date1904 else datetime(1899, 12, 30)
            strings = []
            if "xl/sharedStrings.xml" in archive.namelist():
                strings = [
                    "".join(part.text or "" for part in item.iter(f"{_NS}t"))
                    for item in _xml(archive, "xl/sharedStrings.xml").findall(
                        f"{_NS}si"
                    )
                ]
            return _worksheet_csv(_xml(archive, sheet_name), strings, epoch)
    except ActivationExportError:
        raise
    except (
        BadZipFile,
        KeyError,
        ValueError,
        InvalidOperation,
        OverflowError,
        ParseError,
        DefusedXmlException,
    ) as exc:
        raise ActivationExportError("Invalid activation workbook") from exc

"""Shared comma-separated value encoding for browser and file exports."""

import csv
import io
from collections.abc import Iterable, Iterator, Sequence


def encode_csv_cell(value: object) -> object:
    """Prevent spreadsheet interpretation of text while preserving numeric cells."""
    if isinstance(value, str) and value.startswith(
        ("=", "+", "-", "@", "\t", "\r", "\n")
    ):
        return "'" + value
    return value


def csv_lines(rows: Iterable[Sequence[object]]) -> Iterator[str]:
    """Encode bounded rows using the same cell policy as file exports."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    for row in rows:
        writer.writerow([encode_csv_cell(value) for value in row])
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)

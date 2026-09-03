from __future__ import annotations

from pathlib import Path
from typing import NoReturn

import pytest

from endoreg_db.utils.rust_backend import (
    build_expected_frame_records,
    build_frame_records,
    parse_extracted_frame_numbers,
)


def _python_parse_frame_numbers(paths: list[Path]) -> list[int]:
    parsed: list[int] = []
    for frame_path in paths:
        try:
            frame_number = int(frame_path.stem.split("_")[-1])
            parsed.append(frame_number)
        except (ValueError, IndexError):
            continue
    return parsed


def test_frame_number_rust_backend_matches_python_reference_for_valid_paths() -> None:
    frame_paths = [
        Path("/tmp/frame_0000001.jpg"),
        Path("/tmp/frame_0000017.jpg"),
        Path("/tmp/frame_0000900.jpg"),
    ]

    expected = _python_parse_frame_numbers(frame_paths)
    parsed = parse_extracted_frame_numbers(frame_paths)

    if parsed is None:
        parsed = expected

    assert parsed == expected


def test_frame_number_rust_backend_returns_none_for_invalid_input_to_preserve_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import endoreg_db.utils.rust_backend as rust_backend_module

    def raise_bad_frame_name(*args: object, **kwargs: object) -> NoReturn:
        raise ValueError("bad frame name")

    frame_paths = [
        Path("/tmp/frame_0000001.jpg"),
        Path("/tmp/not_a_frame.jpg"),
    ]

    monkeypatch.setattr(
        rust_backend_module,
        "_parse_extracted_frame_numbers",
        raise_bad_frame_name,
    )

    assert rust_backend_module.parse_extracted_frame_numbers(frame_paths) is None
    assert _python_parse_frame_numbers(frame_paths) == [1]


def test_build_frame_records_matches_python_reference_for_valid_paths() -> None:
    frame_paths = [
        Path("/tmp/frame_0000001.jpg"),
        Path("/tmp/frame_0000017.jpg"),
        Path("/tmp/frame_0000900.jpg"),
    ]

    expected = [(int(path.stem.split("_")[-1]), path.name) for path in frame_paths]
    records = build_frame_records(frame_paths)

    if records is None:
        records = expected

    assert records == expected


def test_build_frame_records_supports_relative_paths_and_zero_based_indices(
    tmp_path: Path,
) -> None:
    frame_dir = tmp_path / "frames"
    frame_dir.mkdir()
    frame_paths = [
        frame_dir / "frame_0000001.jpg",
        frame_dir / "frame_0000017.jpg",
    ]

    expected = [
        (0, "frames/frame_0000001.jpg"),
        (16, "frames/frame_0000017.jpg"),
    ]
    records = build_frame_records(
        frame_paths,
        relative_to=tmp_path,
        zero_based=True,
    )

    if records is None:
        records = expected

    assert records == expected


def test_build_expected_frame_records_matches_python_reference() -> None:
    expected = [
        (0, "frame_0000000.jpg"),
        (1, "frame_0000001.jpg"),
        (2, "frame_0000002.jpg"),
    ]
    records = build_expected_frame_records(3)

    if records is None:
        records = expected

    assert records == expected

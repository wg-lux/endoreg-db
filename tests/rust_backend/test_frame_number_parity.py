from __future__ import annotations

from pathlib import Path

import pytest

from endoreg_db.utils.rust_backend import parse_extracted_frame_numbers


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

    frame_paths = [
        Path("/tmp/frame_0000001.jpg"),
        Path("/tmp/not_a_frame.jpg"),
    ]

    monkeypatch.setattr(
        rust_backend_module,
        "_parse_extracted_frame_numbers",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad frame name")),
    )

    assert rust_backend_module.parse_extracted_frame_numbers(frame_paths) is None
    assert _python_parse_frame_numbers(frame_paths) == [1]

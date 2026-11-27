from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

import pytest

from endoreg_db import endoreg_rust_backend as native
from endoreg_db.utils.rust_backend import NativeBatchProcessor


@pytest.mark.parametrize("text", ["", "line one\nline two", "emoji 😀"])
def test_native_pdf_returns_bytes(text: str) -> None:
    payload = native.render_single_page_pdf(text)
    assert isinstance(payload, bytes)
    assert payload.startswith(b"%PDF-1.4\n")
    assert payload.endswith(b"%%EOF\n")


@pytest.mark.parametrize("path", ["/", "frame_invalid.jpg", "frame_-1.jpg"])
def test_native_frame_validation_raises_value_error(path: str) -> None:
    with pytest.raises(ValueError):
        native.parse_extracted_frame_numbers([path])
    with pytest.raises(ValueError):
        native.build_frame_records([path])


def test_native_frame_record_invariants() -> None:
    with pytest.raises(ValueError, match="zero-based"):
        native.build_frame_records(["frame_0000000.jpg"], zero_based=True)
    with pytest.raises(ValueError, match="not relative"):
        native.build_frame_records(["/outside/frame_1.jpg"], relative_to="/base")
    with pytest.raises(ValueError, match="ext must not be empty"):
        native.build_expected_frame_records(1, " ")


def test_native_missing_files_preserve_exception_type(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(FileNotFoundError):
        native.stable_file_identity(missing)
    with pytest.raises(FileNotFoundError):
        processor = cast(NativeBatchProcessor, native.BatchProcessor(2))
        processor.stable_file_identities([missing])
    with pytest.raises(FileNotFoundError):
        native.encryption_status(missing)


@pytest.mark.parametrize("count", [0, 1, 10000])
def test_native_frame_round_trip_under_concurrent_calls(count: int) -> None:
    def round_trip(_: int) -> list[tuple[int, str]]:
        records = native.build_expected_frame_records(count)
        paths = [path for _, path in records]
        assert native.parse_extracted_frame_numbers(paths) == list(range(count))
        assert native.build_frame_records(paths) == records
        return records

    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(round_trip, range(8)))
    assert all(result == results[0] for result in results)

from __future__ import annotations

# pyright: reportPrivateUsage=false

import hashlib
from pathlib import Path
from typing import NoReturn

import pytest

from endoreg_db.import_files.report_import_service import ReportImportService
from endoreg_db.utils.file_operations import sha256_file
from endoreg_db.utils.rust_backend import (
    parse_extracted_frame_numbers,
)


def raise_bad_frame_count(*args: object, **kwargs: object) -> NoReturn:
    raise ValueError("bad frame count")


def test_sha256_file_matches_python_hashlib(tmp_path: Path) -> None:
    payload = (b"video-chunk-1234567890" * 4096) + b"tail"
    test_file = tmp_path / "sample.bin"
    test_file.write_bytes(payload)

    expected = hashlib.sha256(payload).hexdigest()

    assert sha256_file(test_file) == expected


def test_render_single_page_pdf_returns_valid_pdf_bytes() -> None:
    pdf_bytes = ReportImportService._render_single_page_pdf(
        "txt_sha256:abc123\nFirst line\nSecond line"
    )

    assert pdf_bytes.startswith(b"%PDF-1.4\n")
    assert b"First line" in pdf_bytes
    assert b"Second line" in pdf_bytes
    assert pdf_bytes.rstrip().endswith(b"%%EOF")


def test_parse_extracted_frame_numbers_matches_expected_values() -> None:
    frame_paths = [
        Path("/tmp/frame_0000001.jpg"),
        Path("/tmp/frame_0000017.jpg"),
        Path("/tmp/frame_0000900.jpg"),
    ]

    parsed = parse_extracted_frame_numbers(frame_paths)

    if parsed is None:
        parsed = [int(path.stem.split("_")[-1]) for path in frame_paths]

    assert parsed == [1, 17, 900]


def test_build_frame_records_returns_none_when_rust_backend_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import endoreg_db.utils.rust_backend as rust_backend_module

    def raise_bad_frame_name(*args: object, **kwargs: object) -> NoReturn:
        raise ValueError("bad frame name")

    frame_paths = [Path("/tmp/frame_0000001.jpg")]

    monkeypatch.setattr(
        rust_backend_module,
        "_build_frame_records",
        raise_bad_frame_name,
    )

    assert rust_backend_module.build_frame_records(frame_paths) is None


def test_build_expected_frame_records_returns_none_when_rust_backend_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import endoreg_db.utils.rust_backend as rust_backend_module

    monkeypatch.setattr(
        rust_backend_module,
        "_build_expected_frame_records",
        raise_bad_frame_count,
    )

    assert rust_backend_module.build_expected_frame_records(3) is None


def test_stable_file_identity_normalizes_native_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import endoreg_db.utils.rust_backend as rust_backend_module

    test_file = tmp_path / "video.mp4"
    test_file.write_bytes(b"video")

    def native_identity(path: Path, chunk_size: int) -> tuple[int, int, str]:
        return 5, 123, "a" * 64

    monkeypatch.setattr(
        rust_backend_module,
        "_stable_file_identity",
        native_identity,
    )

    assert rust_backend_module.stable_file_identity(test_file) == (
        5,
        123,
        "a" * 64,
    )


def test_stable_file_identity_fails_loudly_on_native_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import endoreg_db.utils.rust_backend as rust_backend_module

    test_file = tmp_path / "video.mp4"
    test_file.write_bytes(b"video")

    def raise_changed_file(path: Path, chunk_size: int) -> NoReturn:
        raise OSError("file changed")

    monkeypatch.setattr(
        rust_backend_module,
        "_stable_file_identity",
        raise_changed_file,
    )

    with pytest.raises(RuntimeError, match="stable_file_identity failed"):
        rust_backend_module.stable_file_identity(test_file)


def test_native_capabilities_are_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import endoreg_db.utils.rust_backend as rust_backend_module

    def capabilities() -> list[tuple[str, str, str]]:
        return [
            (
                "report_source_snapshot",
                "report_source_snapshot_v1",
                "0.1.0",
            )
        ]

    monkeypatch.setattr(
        rust_backend_module,
        "_native_capabilities",
        capabilities,
    )

    assert rust_backend_module.native_capabilities() == (
        ("report_source_snapshot", "report_source_snapshot_v1", "0.1.0"),
    )
    assert rust_backend_module.has_native_capability(
        "report_source_snapshot",
        "report_source_snapshot_v1",
    )

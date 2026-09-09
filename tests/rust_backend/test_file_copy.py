from __future__ import annotations

from pathlib import Path

from endoreg_db.utils.rust_backend import copy_file_descriptor_to_path


def test_copy_file_descriptor_to_path_matches_source_bytes(tmp_path: Path) -> None:
    source_path = tmp_path / "source.bin"
    target_path = tmp_path / "target.bin"
    payload = (b"0123456789abcdef" * 8192) + b"tail"
    source_path.write_bytes(payload)

    with source_path.open("rb") as source:
        copied = copy_file_descriptor_to_path(
            source_fd=source.fileno(),
            target_path=target_path,
            chunk_size=4096,
        )

    if copied is not None:
        assert copied == len(payload)
        assert target_path.read_bytes() == payload

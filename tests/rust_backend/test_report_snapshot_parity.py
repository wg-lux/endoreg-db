from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from endoreg_db.utils.file_operations import (
    atomic_report_source_snapshot,
)
from endoreg_db.utils.rust_backend import stable_snapshot_to_path


@pytest.mark.parametrize("chunk_size", [1, 7, 4096, 1024 * 1024])
def test_report_snapshot_matches_source_and_python_hash(
    tmp_path: Path,
    chunk_size: int,
) -> None:
    payload = (b"%PDF-1.4\nsnapshot-parity\n" * 4096) + b"%%EOF\n"
    source = tmp_path / "source.pdf"
    destination = tmp_path / "snapshot.pdf"
    source.write_bytes(payload)

    snapshot = atomic_report_source_snapshot(
        source=source,
        destination=destination,
        chunk_size=chunk_size,
    )

    assert snapshot.contract_version == "report_source_snapshot_v1"
    assert snapshot.path == destination
    assert snapshot.size_bytes == len(payload)
    assert snapshot.sha256 == hashlib.sha256(payload).hexdigest()
    assert destination.read_bytes() == payload


def test_native_report_snapshot_matches_python_reference_when_available(
    tmp_path: Path,
) -> None:
    payload = b"%PDF-1.4\nnative-snapshot\n%%EOF\n"
    source = tmp_path / "source.pdf"
    target = tmp_path / "native-target.pdf"
    source.write_bytes(payload)

    identity = stable_snapshot_to_path(source, target, 5)

    if identity is None:
        pytest.skip("installed extension does not expose stable_snapshot_to_path")
    assert identity[0] == len(payload)
    assert identity[2] == hashlib.sha256(payload).hexdigest()
    assert target.read_bytes() == payload


def test_report_snapshot_refuses_to_overwrite_destination(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    destination = tmp_path / "snapshot.pdf"
    source.write_bytes(b"new")
    destination.write_bytes(b"existing")

    with pytest.raises(FileExistsError, match="already exists"):
        atomic_report_source_snapshot(
            source=source,
            destination=destination,
        )

    assert destination.read_bytes() == b"existing"


def test_report_snapshot_rejects_symbolic_link_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import endoreg_db.utils.file_operations as file_operations

    real_source = tmp_path / "real.pdf"
    linked_source = tmp_path / "linked.pdf"
    destination = tmp_path / "snapshot.pdf"
    real_source.write_bytes(b"%PDF-1.4\n%%EOF\n")
    linked_source.symlink_to(real_source)

    def no_native_snapshot(
        source_path: Path,
        target_path: Path,
        chunk_size: int,
    ) -> None:
        return None

    monkeypatch.setattr(
        file_operations,
        "rust_stable_snapshot_to_path",
        no_native_snapshot,
    )

    with pytest.raises(OSError):
        atomic_report_source_snapshot(
            source=linked_source,
            destination=destination,
        )

    assert not destination.exists()

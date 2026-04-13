from __future__ import annotations

from pathlib import Path


STREAMABLE_MEDIA = Path(
    "/home/admin/endoreg-db/endoreg_db/services/streamable_media.py"
)


def test_streamable_materialization_never_moves_canonical_source() -> None:
    source = STREAMABLE_MEDIA.read_text(encoding="utf-8")
    assert "atomic_copy_file(" in source
    assert "atomic_move_file(" not in source

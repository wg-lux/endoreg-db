from unittest.mock import patch

import pytest
from django.db.models.fields.files import FieldFile

from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.utils.hashs import get_file_hash


@pytest.fixture
def stored_file() -> FieldFile:
    return RawPdfFile(file="integrity.pdf").file


@pytest.mark.parametrize(
    ("size", "chunks", "final_size"),
    [
        (4, [b"abc"], 4),
        (2, [b"abc"], 2),
        (3, [b"abc"], 4),
        (0, [], 1),
        (-1, [], -1),
    ],
)
def test_hash_rejects_inconsistent_stored_stream(
    stored_file: FieldFile, size: int, chunks: list[bytes], final_size: int
) -> None:
    with (
        patch(
            "endoreg_db.utils.storage_streaming.field_file_size",
            side_effect=[size, final_size],
        ),
        patch(
            "endoreg_db.utils.storage_streaming.iter_field_file_bytes",
            return_value=iter(chunks),
        ),
        pytest.raises(ValueError, match="plaintext size"),
    ):
        get_file_hash(stored_file)


@pytest.mark.parametrize("chunks", [[b"abc"], [b"a", b"", b"bc"]])
def test_hash_accepts_complete_stream(
    stored_file: FieldFile, chunks: list[bytes]
) -> None:
    with (
        patch("endoreg_db.utils.storage_streaming.field_file_size", return_value=3),
        patch(
            "endoreg_db.utils.storage_streaming.iter_field_file_bytes",
            return_value=iter(chunks),
        ),
    ):
        assert get_file_hash(stored_file) == (
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        )


def test_hash_propagates_authenticated_stream_failure(stored_file: FieldFile) -> None:
    with (
        patch("endoreg_db.utils.storage_streaming.field_file_size", return_value=3),
        patch(
            "endoreg_db.utils.storage_streaming.iter_field_file_bytes",
            side_effect=OSError("authentication failed"),
        ),
        pytest.raises(OSError, match="authentication failed"),
    ):
        get_file_hash(stored_file)

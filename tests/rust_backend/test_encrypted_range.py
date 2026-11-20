from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from typing import NoReturn

import pytest

from endoreg_db.utils.encryption.encryption import encrypt_stream
from endoreg_db.utils.rust_backend import decrypt_encrypted_file_range


def _write_encrypted_fixture(
    path: Path,
    *,
    plaintext: bytes,
    master_key: bytes,
) -> None:
    with path.open("wb") as destination:
        encrypt_stream(
            BytesIO(plaintext),
            destination,
            master_key=master_key,
            chunk_size=64 * 1024,
        )


@pytest.mark.unit
def test_native_encrypted_range_matches_python_plaintext_under_concurrency(
    tmp_path: Path,
) -> None:
    master_key = b"m" * 32
    plaintext = bytes(index % 251 for index in range(2 * 1024 * 1024 + 113))
    encrypted_path = tmp_path / "concurrent-video.mp4"
    _write_encrypted_fixture(
        encrypted_path,
        plaintext=plaintext,
        master_key=master_key,
    )
    ranges = [
        (0, 131_071),
        (31_337, 412_345),
        (524_288, 1_048_575),
        (777_777, 1_777_777),
        (len(plaintext) - 200_000, len(plaintext) - 1),
    ]

    def decrypt(selected_range: tuple[int, int]) -> bytes | None:
        start, end = selected_range
        return decrypt_encrypted_file_range(
            path=encrypted_path,
            master_key=master_key,
            start=start,
            end=end,
        )

    first = decrypt(ranges[0])
    if first is None:
        pytest.skip("Rust backend is unavailable in this environment")

    with ThreadPoolExecutor(max_workers=len(ranges)) as executor:
        results = list(executor.map(decrypt, ranges))

    for (start, end), result in zip(ranges, results, strict=True):
        assert result == plaintext[start : end + 1]


@pytest.mark.unit
def test_native_encrypted_range_errors_fail_loudly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import endoreg_db.utils.rust_backend as rust_backend_module

    encrypted_path = tmp_path / "video.mp4"
    encrypted_path.write_bytes(b"ciphertext")

    def fail_native(
        path: Path,
        master_key: bytes,
        start: int,
        end: int,
    ) -> NoReturn:
        del path, master_key, start, end
        raise ValueError("authentication failed")

    monkeypatch.setattr(
        rust_backend_module,
        "_decrypt_encrypted_file_range",
        fail_native,
    )

    with pytest.raises(RuntimeError, match="authentication failed"):
        rust_backend_module.decrypt_encrypted_file_range(
            path=encrypted_path,
            master_key=b"k" * 32,
            start=0,
            end=3,
        )

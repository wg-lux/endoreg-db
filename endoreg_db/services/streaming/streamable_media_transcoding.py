from __future__ import annotations

from pathlib import Path

from endoreg_db.utils.encryption.encryption import MAGIC
from endoreg_db.utils.rust_backend import is_lx_encrypted_file

# Compatibility export; file-format probes are implemented exclusively in Rust.
LX_ENCRYPTED_MAGIC = MAGIC


def is_encrypted_file(path: Path) -> bool:
    return is_lx_encrypted_file(path)

"""Canonical import surface for audited filesystem mutations.

The implementation remains in the legacy module while callers migrate to this
package. Re-exporting the same function objects preserves structured logging,
atomicity, and monkeypatch compatibility without duplicating mutation logic.
"""

from endoreg_db.utils.file_operations import (
    atomic_copy_file,
    atomic_move_file,
    atomic_write_file,
    safe_unlink_file,
)

__all__ = [
    "atomic_copy_file",
    "atomic_move_file",
    "atomic_write_file",
    "safe_unlink_file",
]

from __future__ import annotations

import posixpath
from pathlib import Path
from typing import BinaryIO, Literal, Protocol, cast

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from endoreg_db.models import ModelMeta

MANAGED_STUB_WEIGHT_PAYLOAD = b"stub-weights"


class _BinaryReadableStorage(Protocol):
    def open(self, name: str, mode: Literal["rb"]) -> BinaryIO: ...


def is_managed_stub_weight_name(weights_name: str | None) -> bool:
    if not weights_name:
        return False
    return "stub" in Path(weights_name).name.lower()


def cleanup_managed_stub_weight_collisions(weights_name: str) -> None:
    """
    Remove orphaned default-storage collision variants for managed stub weights.

    This only deletes files when all of the following are true:
    - the file is a collision variant of the managed stub name
    - no ModelMeta currently references it
    - the file content exactly matches the tiny stub payload
    """
    directory = posixpath.dirname(weights_name)
    filename = posixpath.basename(weights_name)
    stem = Path(filename).stem
    suffix = Path(filename).suffix

    try:
        _, files = default_storage.listdir(directory)
    except Exception:
        return

    referenced_names = set(
        ModelMeta.objects.exclude(weights="")
        .filter(weights__startswith=f"{directory}/")
        .values_list("weights", flat=True)
    )

    for candidate_name in files:
        if candidate_name == filename:
            continue
        if not candidate_name.startswith(f"{stem}_") or not candidate_name.endswith(
            suffix
        ):
            continue

        candidate_path = posixpath.join(directory, candidate_name)
        if candidate_path in referenced_names:
            continue

        try:
            storage = cast(_BinaryReadableStorage, default_storage)
            with storage.open(candidate_path, "rb") as handle:
                if handle.read() != MANAGED_STUB_WEIGHT_PAYLOAD:
                    continue
            default_storage.delete(candidate_path)
        except Exception:
            continue


def ensure_managed_stub_weights(
    meta: ModelMeta,
    *,
    suffix: str | None = None,
) -> None:
    """
    Ensure a test-managed stub ModelMeta points to an existing tiny weights file.

    Real/non-stub weights are intentionally left untouched so production-like
    missing-weight failures still fail loudly.
    """
    weights_name = meta.weights.name if meta.weights else ""
    if weights_name:
        if not is_managed_stub_weight_name(weights_name):
            return
    else:
        if suffix is None:
            return
        weights_name = f"model_weights/{suffix}"
        meta.weights.name = weights_name
        meta.save(update_fields=["weights"])

    cleanup_managed_stub_weight_collisions(weights_name)
    storage = meta.weights.storage
    is_encrypted = getattr(storage, "is_encrypted", None)
    if (
        storage.exists(weights_name)
        and callable(is_encrypted)
        and not is_encrypted(weights_name)
    ):
        storage.delete(weights_name)
    if not storage.exists(weights_name):
        stored_name = storage.save(
            weights_name,
            ContentFile(MANAGED_STUB_WEIGHT_PAYLOAD),
        )
        if stored_name != weights_name:
            raise RuntimeError(
                "Managed stub weights were stored under an unexpected name"
            )
    cleanup_managed_stub_weight_collisions(weights_name)

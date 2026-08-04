from __future__ import annotations

import posixpath
from pathlib import Path

from django.core.files.storage import default_storage

from endoreg_db.models import ModelMeta
from endoreg_db.utils.file_operations import atomic_write_file

MANAGED_STUB_WEIGHT_PAYLOAD = b"stub-weights"


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
            with default_storage.open(candidate_path, "rb") as handle:
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
    if not default_storage.exists(weights_name):
        atomic_write_file(
            destination=Path(default_storage.path(weights_name)),
            content=[MANAGED_STUB_WEIGHT_PAYLOAD],
            required_bytes=len(MANAGED_STUB_WEIGHT_PAYLOAD),
        )
    cleanup_managed_stub_weight_collisions(weights_name)

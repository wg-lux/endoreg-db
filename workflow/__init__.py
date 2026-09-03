"""Packaged Snakemake workflows for supervised endoreg-db batch execution."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import Final


_REQUIRED_ASSETS: Final[tuple[str, ...]] = (
    "Snakefile",
    "profiles/offline-batch/config.yaml",
    "rules/common.smk",
    "rules/report_import.smk",
    "rules/video_hls_materialization.smk",
    "rules/video_import.smk",
    "rules/video_transcode.smk",
    "scripts/import_common.py",
    "scripts/run_report_import.py",
    "scripts/run_video_hls_materialization.py",
    "scripts/run_video_import.py",
    "scripts/run_video_transcode.py",
)


def packaged_workflow_root() -> Path:
    """
    Return the unpacked package root required by Snakemake.

    Python wheel installations are unpacked into ``site-packages``. Refuse
    non-filesystem resource loaders because Snakemake must resolve includes and
    stage scripts as stable paths for the complete child-process lifetime.
    """
    resource_root = files(__name__)
    if not isinstance(resource_root, Path):
        raise RuntimeError(
            "The workflow package must be installed as unpacked filesystem assets."
        )
    missing = [
        relative_path
        for relative_path in _REQUIRED_ASSETS
        if not resource_root.joinpath(relative_path).is_file()
    ]
    if missing:
        raise RuntimeError(
            "The installed workflow package is incomplete: " + ", ".join(missing)
        )
    return resource_root


__all__ = ["packaged_workflow_root"]

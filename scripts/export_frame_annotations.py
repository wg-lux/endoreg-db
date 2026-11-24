"""Run a reproducible frame annotation export from a YAML config.

The YAML file is validated against the lx_dtypes
``VideoFrameAnnotationExportConfigPayload`` contract and passed to the
in-process ``AnnotationExporterClient``, the same exporter used by
``manage.py export_frame_annot``.

Usage::

    # Check the config without touching the database or filesystem.
    devenv shell -- python scripts/export_frame_annotations.py \\
        --config scripts/examples/frame_annotation_export.yml --validate-only

    # Run the export; --output-dir reuses one config for several runs.
    devenv shell -- python scripts/export_frame_annotations.py \\
        --config scripts/examples/frame_annotation_export.yml \\
        --output-dir data/export/frame_annotations_run_002

``DJANGO_SETTINGS_MODULE`` must be set for a real export; the devenv shell
provides it.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from lx_dtypes.models.contracts import export_config, export_result
from pydantic import ValidationError


def load_config(config_path: Path, *, output_dir: Path | None = None) -> export_config:
    """Validate the YAML config and apply the optional output directory override."""
    config = export_config.from_yaml(config_path)
    if output_dir is not None:
        config = replace(config, output_dir=output_dir)
    return config


def describe_config(config: export_config) -> str:
    """Summarize the export selection and destination for operator review."""
    scope = (
        f"center_key={config.center_key}"
        if config.center_key
        else "all_centers"
        if config.all_centers
        else "unscoped"
    )
    return "\n".join(
        (
            f"output_path: {config.output_path}",
            f"output_dir: {config.output_dir}",
            f"format: {config.output_format} ({config.export_profile})",
            f"scope: {scope}",
            f"information_source_name: {config.information_source_name}",
            f"only_validated: {config.only_validated}",
            f"export_frames: {config.export_frames}",
            f"export_videos: {config.export_videos}",
        )
    )


def describe_result(result: export_result) -> str:
    """Summarize where the export wrote its table and media assets."""
    return "\n".join(
        (
            f"rows: {result.row_count}",
            f"table: {result.output_path}",
            f"frames: {result.exported_frame_count} -> {result.frame_output_dir}",
            f"videos: {result.exported_video_count} -> {result.video_output_dir}",
        )
    )


def run_export(config: export_config) -> export_result:
    """Run the export through the in-process exporter; requires a configured Django."""
    from endoreg_db.export.frames.export_frames_with_labels import (
        AnnotationExporterClient,
    )

    return AnnotationExporterClient().run_export(config)


def _setup_django() -> None:
    if "DJANGO_SETTINGS_MODULE" not in os.environ:
        raise SystemExit(
            "DJANGO_SETTINGS_MODULE is not set; run inside `devenv shell`."
        )
    import django

    django.setup()


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a reproducible frame annotation export from a YAML config."
    )
    parser.add_argument(
        "--config", type=Path, required=True, help="YAML export configuration."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Override output_dir from the config for this run.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate and print the config without exporting.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        config = load_config(args.config, output_dir=args.output_dir)
    except (FileNotFoundError, ValidationError) as exc:
        print(f"Invalid export config {args.config}: {exc}", file=sys.stderr)
        return 2

    print(describe_config(config))
    if args.validate_only:
        return 0

    _setup_django()
    from endoreg_db.export.frames.export_frames_with_labels import (
        ExportJobFailedError,
    )

    try:
        result = run_export(config)
    except ExportJobFailedError as exc:
        print(f"Export failed: {exc}", file=sys.stderr)
        return 1

    print(describe_result(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())

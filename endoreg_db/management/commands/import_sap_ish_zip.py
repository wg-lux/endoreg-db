from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from endoreg_db.services.hub.ingest import (
    resolve_declared_upload_center,
    resolve_default_center,
    process_preanonymized_watcher_file,
)

from endoreg_db.services.sap_ish_import import convert_sap_ish_zip_to_preanonymized_drop
from endoreg_db.utils.filesystem.file_operations import (
    atomic_write_file,
    ensure_directory,
)
from endoreg_db.utils.filesystem.paths import (
    SAP_IMPORT_DROP_DIR,
    WATCHER_PREANONYMIZED_DROP_DIR,
    build_manifest_path,
    ensure_within_data_root,
)


def _resolve_declared_upload_center(*, center_key: str | None, center_name: str | None):
    return resolve_declared_upload_center(
        center_key=center_key,
        center_name=center_name,
    )


def _resolve_default_center():
    return resolve_default_center()


def _process_preanonymized_watcher_file(
    *, file_path, center, source_system: str
) -> None:
    process_preanonymized_watcher_file(
        file_path=file_path,
        center=center,
        source_system=source_system,
    )


class Command(BaseCommand):
    help = (
        "Convert a SAP IS-H zip export of tab-separated .txt tables into "
        "preanonymized watcher files (.txt + .json). Optionally process them "
        "immediately through the existing watcher ingest path."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "zip_path",
            type=str,
            help="Path to the SAP IS-H zip archive containing tab-separated .txt tables.",
        )
        parser.add_argument(
            "--output_dir",
            type=str,
            default=str(WATCHER_PREANONYMIZED_DROP_DIR),
            help=(
                "Directory for generated watcher-ready .txt/.json pairs. "
                f"Default: {WATCHER_PREANONYMIZED_DROP_DIR}"
            ),
        )
        parser.add_argument(
            "--source_system",
            type=str,
            default="sap_ish",
            help="Value written to external_id_origin and source_system.",
        )
        parser.add_argument(
            "--center_name",
            type=str,
            default="",
            help="Optional center name to write into sidecars and use for processing.",
        )
        parser.add_argument(
            "--center_key",
            type=str,
            default="",
            help="Optional center key to write into sidecars and use for processing.",
        )
        parser.add_argument(
            "--process",
            action="store_true",
            default=False,
            help=(
                "Immediately process generated files through "
                "process_preanonymized_watcher_file."
            ),
        )
        parser.add_argument(
            "--manifest_path",
            type=str,
            default="",
            help="Optional manifest path under the protected migration manifest tier.",
        )

    def handle(self, *args, **options) -> None:
        zip_path = Path(options["zip_path"]).expanduser().resolve()
        output_dir = ensure_within_data_root(
            Path(options["output_dir"]).expanduser().resolve()
        )
        source_system = str(options["source_system"]).strip() or "sap_ish"
        center_name = str(options.get("center_name") or "").strip() or None
        center_key = str(options.get("center_key") or "").strip() or None
        should_process = bool(options["process"])
        manifest_path_raw = str(options.get("manifest_path") or "").strip()

        if not zip_path.exists():
            raise CommandError(f"Zip archive does not exist: {zip_path}")
        if not zip_path.is_relative_to(SAP_IMPORT_DROP_DIR):
            self.stdout.write(
                self.style.WARNING(
                    f"SAP archive is outside managed sap drop tier: {zip_path}"
                )
            )

        center = None
        if center_name or center_key:
            center, center_resolution_error = _resolve_declared_upload_center(
                center_key=center_key,
                center_name=center_name,
            )
            if center_resolution_error:
                raise CommandError(center_resolution_error)
        elif should_process:
            center = _resolve_default_center()
            if center is None:
                raise CommandError(
                    "No default center is configured for immediate processing"
                )

        result = convert_sap_ish_zip_to_preanonymized_drop(
            zip_path=zip_path,
            output_dir=output_dir,
            source_system=source_system,
            center_name=center_name,
            center_key=center_key,
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Generated "
                f"{len(result.generated_files)} watcher file pair(s) from "
                f"{len(result.matched_source_files)} supported SAP table file(s)"
            )
        )
        if result.skipped_source_files:
            self.stdout.write(
                self.style.WARNING(
                    "Skipped unsupported table files: "
                    + ", ".join(path.name for path in result.skipped_source_files)
                )
            )

        for generated_file in result.generated_files:
            self.stdout.write(
                f"- {generated_file.carrier_path.name} ({generated_file.document_type})"
            )

        if should_process:
            processed_count = 0
            for generated_file in result.generated_files:
                _process_preanonymized_watcher_file(
                    file_path=generated_file.carrier_path,
                    center=center,
                    source_system=source_system,
                )
                processed_count += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"Processed {processed_count} generated file(s) through watcher ingest"
                )
            )

        manifest_path = (
            ensure_within_data_root(Path(manifest_path_raw).expanduser().resolve())
            if manifest_path_raw
            else build_manifest_path(
                command_name="import_sap_ish_zip",
                stem=f"{zip_path.stem}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
            )
        )
        ensure_directory(manifest_path.parent)
        manifest = {
            "command": "import_sap_ish_zip",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "zip_path": str(zip_path),
            "output_dir": str(output_dir),
            "source_system": source_system,
            "center_name": center_name,
            "center_key": center_key,
            "process": should_process,
            "generated_files": [
                {
                    "carrier_path": str(generated_file.carrier_path),
                    "sidecar_path": str(generated_file.sidecar_path),
                    "document_type": generated_file.document_type,
                }
                for generated_file in result.generated_files
            ],
            "matched_source_files": [str(path) for path in result.matched_source_files],
            "skipped_source_files": [str(path) for path in result.skipped_source_files],
        }
        atomic_write_file(
            destination=manifest_path,
            content=[json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")],
        )
        self.stdout.write(f"Manifest written to {manifest_path}")

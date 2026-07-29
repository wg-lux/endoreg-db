from __future__ import annotations

from pathlib import Path
from types import NoneType
from typing import TypeAlias, TypedDict, Unpack

from django.core.management.base import BaseCommand, CommandError, CommandParser

from endoreg_db.models.administration.center.center import Center
from endoreg_db.services.hub.ingest import (
    process_preanonymized_watcher_file,
    resolve_declared_upload_center,
    resolve_default_center,
)
from endoreg_db.services.sap_ish_import import (
    convert_sap_ish_txt_directory_to_preanonymized_drop,
)
from endoreg_db.services.sap_ish_clinical import persist_sap_ish_clinical_rows
from endoreg_db.utils.paths import (
    WATCHER_PREANONYMIZED_DROP_DIR,
    ensure_within_data_root,
)

JsonNull: TypeAlias = NoneType


class ImportSapIshTxtOptions(TypedDict):
    source_dir: str
    output_dir: str
    source_system: str
    center_name: str
    center_key: str
    process: bool


def _resolve_processing_center(
    *,
    center_key: str | None,
    center_name: str | None,
    should_process: bool,
) -> Center | JsonNull:
    if center_name or center_key:
        center, center_resolution_error = resolve_declared_upload_center(
            center_key=center_key,
            center_name=center_name,
        )
        if center_resolution_error:
            raise CommandError(center_resolution_error)
        return center
    if not should_process:
        return None
    center = resolve_default_center()
    if center is None:
        raise CommandError("No default center is configured for immediate processing")
    return center


class Command(BaseCommand):
    help = (
        "Convert a directory of SAP IS-H tab-separated .txt exports into "
        "preanonymized watcher files (.txt + .yaml). With --process, persist "
        "them through the existing Django watcher ingest service."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "source_dir",
            type=str,
            help="Directory containing SAP IS-H tab-separated .txt tables.",
        )
        parser.add_argument(
            "--output_dir",
            type=str,
            default=str(WATCHER_PREANONYMIZED_DROP_DIR),
            help=(
                "Directory for generated watcher-ready .txt/.yaml pairs. "
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
            help="Center name to write into sidecars and use for processing.",
        )
        parser.add_argument(
            "--center_key",
            type=str,
            default="",
            help="Center key to write into sidecars and use for processing.",
        )
        parser.add_argument(
            "--process",
            action="store_true",
            default=False,
            help="Persist generated files through the preanonymized watcher service.",
        )

    def handle(
        self,
        *args: str,
        **options: Unpack[ImportSapIshTxtOptions],
    ) -> None:
        source_dir = Path(options["source_dir"]).expanduser().resolve()
        if not source_dir.exists():
            raise CommandError(f"Source directory does not exist: {source_dir}")
        if not source_dir.is_dir():
            raise CommandError(f"Source path is not a directory: {source_dir}")

        output_dir = ensure_within_data_root(
            Path(options["output_dir"]).expanduser().resolve()
        )
        source_system = options["source_system"].strip() or "sap_ish"
        center_name = options["center_name"].strip() or None
        center_key = options["center_key"].strip() or None
        should_process = options["process"]
        center = _resolve_processing_center(
            center_key=center_key,
            center_name=center_name,
            should_process=should_process,
        )
        effective_center_name = center_name or (
            center.name if center is not None else None
        )
        effective_center_key = center_key or (
            str(center.center_key) if center is not None else None
        )

        result = convert_sap_ish_txt_directory_to_preanonymized_drop(
            source_dir=source_dir,
            output_dir=output_dir,
            source_system=source_system,
            center_name=effective_center_name,
            center_key=effective_center_key,
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

        if not should_process:
            return
        if center is None:
            raise CommandError("A center is required for immediate processing")
        for generated_file in result.generated_files:
            process_preanonymized_watcher_file(
                file_path=generated_file.carrier_path,
                center=center,
                source_system=source_system,
            )
        clinical_result = persist_sap_ish_clinical_rows(
            rows=result.normalized_rows,
            source_system=source_system,
            center=center,
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Persisted {len(result.generated_files)} generated file(s); "
                f"clinical rows={clinical_result.rows_seen}, "
                f"created="
                f"{clinical_result.diseases_created + clinical_result.lab_values_created + clinical_result.medications_created}, "
                f"reused="
                f"{clinical_result.diseases_reused + clinical_result.lab_values_reused + clinical_result.medications_reused}, "
                f"skipped={clinical_result.rows_skipped}"
            )
        )

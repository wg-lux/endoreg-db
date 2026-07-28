from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
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
    SapIshImportResult,
    convert_sap_ish_zip_to_preanonymized_drop,
)
from endoreg_db.utils.file_operations import (
    atomic_write_file,
    ensure_directory,
)
from endoreg_db.utils.paths import (
    SAP_IMPORT_DROP_DIR,
    WATCHER_PREANONYMIZED_DROP_DIR,
    build_manifest_path,
    ensure_within_data_root,
)

JsonNull: TypeAlias = NoneType


class ImportSapIshZipOptions(TypedDict):
    zip_path: str
    output_dir: str
    source_system: str
    center_name: str
    center_key: str
    process: bool
    manifest_path: str


class ImportSapIshGeneratedFileManifest(TypedDict):
    carrier_path: str
    sidecar_path: str
    document_type: str


class ImportSapIshManifest(TypedDict):
    command: str
    created_at: str
    zip_path: str
    output_dir: str
    source_system: str
    center_name: str | JsonNull
    center_key: str | JsonNull
    process: bool
    generated_files: list[ImportSapIshGeneratedFileManifest]
    matched_source_files: list[str]
    skipped_source_files: list[str]


@dataclass(frozen=True, slots=True)
class ImportSapIshZipRequest:
    zip_path: Path
    output_dir: Path
    source_system: str
    center_name: str | JsonNull
    center_key: str | JsonNull
    should_process: bool
    manifest_path_raw: str


def _resolve_declared_upload_center(
    *,
    center_key: str | JsonNull,
    center_name: str | JsonNull,
) -> tuple[Center | JsonNull, str | JsonNull]:
    return resolve_declared_upload_center(
        center_key=center_key,
        center_name=center_name,
    )


def _resolve_default_center() -> Center | JsonNull:
    return resolve_default_center()


def _process_preanonymized_watcher_file(
    *,
    file_path: Path,
    center: Center | JsonNull,
    source_system: str,
) -> None:
    process_preanonymized_watcher_file(
        file_path=file_path,
        center=center,
        source_system=source_system,
    )


def _resolve_request(options: ImportSapIshZipOptions) -> ImportSapIshZipRequest:
    return ImportSapIshZipRequest(
        zip_path=Path(options["zip_path"]).expanduser().resolve(),
        output_dir=ensure_within_data_root(
            Path(options["output_dir"]).expanduser().resolve()
        ),
        source_system=options["source_system"].strip() or "sap_ish",
        center_name=options["center_name"].strip() or None,
        center_key=options["center_key"].strip() or None,
        should_process=options["process"],
        manifest_path_raw=options["manifest_path"].strip(),
    )


def _validate_archive(command: BaseCommand, request: ImportSapIshZipRequest) -> None:
    if not request.zip_path.exists():
        raise CommandError(f"Zip archive does not exist: {request.zip_path}")
    if not request.zip_path.is_relative_to(SAP_IMPORT_DROP_DIR):
        command.stdout.write(
            command.style.WARNING(
                f"SAP archive is outside managed sap drop tier: {request.zip_path}"
            )
        )


def _resolve_processing_center(
    request: ImportSapIshZipRequest,
) -> Center | JsonNull:
    if request.center_name or request.center_key:
        center, center_resolution_error = _resolve_declared_upload_center(
            center_key=request.center_key,
            center_name=request.center_name,
        )
        if center_resolution_error:
            raise CommandError(center_resolution_error)
        return center
    if not request.should_process:
        return None
    center = _resolve_default_center()
    if center is None:
        raise CommandError("No default center is configured for immediate processing")
    return center


def _write_import_summary(command: BaseCommand, result: SapIshImportResult) -> None:
    command.stdout.write(
        command.style.SUCCESS(
            "Generated "
            f"{len(result.generated_files)} watcher file pair(s) from "
            f"{len(result.matched_source_files)} supported SAP table file(s)"
        )
    )
    if result.skipped_source_files:
        command.stdout.write(
            command.style.WARNING(
                "Skipped unsupported table files: "
                + ", ".join(path.name for path in result.skipped_source_files)
            )
        )
    for generated_file in result.generated_files:
        command.stdout.write(
            f"- {generated_file.carrier_path.name} ({generated_file.document_type})"
        )


def _process_generated_files(
    command: BaseCommand,
    *,
    request: ImportSapIshZipRequest,
    result: SapIshImportResult,
    center: Center | JsonNull,
) -> None:
    if not request.should_process:
        return
    processed_count = 0
    for generated_file in result.generated_files:
        _process_preanonymized_watcher_file(
            file_path=generated_file.carrier_path,
            center=center,
            source_system=request.source_system,
        )
        processed_count += 1
    command.stdout.write(
        command.style.SUCCESS(
            f"Processed {processed_count} generated file(s) through watcher ingest"
        )
    )


def _resolve_manifest_path(request: ImportSapIshZipRequest) -> Path:
    if request.manifest_path_raw:
        return ensure_within_data_root(
            Path(request.manifest_path_raw).expanduser().resolve()
        )
    return build_manifest_path(
        command_name="import_sap_ish_zip",
        stem=(
            f"{request.zip_path.stem}_"
            f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        ),
    )


def _build_manifest(
    request: ImportSapIshZipRequest,
    result: SapIshImportResult,
) -> ImportSapIshManifest:
    return {
        "command": "import_sap_ish_zip",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "zip_path": str(request.zip_path),
        "output_dir": str(request.output_dir),
        "source_system": request.source_system,
        "center_name": request.center_name,
        "center_key": request.center_key,
        "process": request.should_process,
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


def _write_manifest(
    command: BaseCommand,
    *,
    request: ImportSapIshZipRequest,
    result: SapIshImportResult,
) -> None:
    manifest_path = _resolve_manifest_path(request)
    ensure_directory(manifest_path.parent)
    manifest = _build_manifest(request, result)
    atomic_write_file(
        destination=manifest_path,
        content=[json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")],
    )
    command.stdout.write(f"Manifest written to {manifest_path}")


class Command(BaseCommand):
    help = (
        "Convert a SAP IS-H zip export of tab-separated .txt tables into "
        "preanonymized watcher files (.txt + .json). Can process them "
        "immediately through the existing watcher ingest path."
    )

    def add_arguments(self, parser: CommandParser) -> None:
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
            help=(
                "Immediately process generated files through "
                "process_preanonymized_watcher_file."
            ),
        )
        parser.add_argument(
            "--manifest_path",
            type=str,
            default="",
            help="Manifest path under the protected migration manifest tier.",
        )

    def handle(
        self,
        *args: str,
        **options: Unpack[ImportSapIshZipOptions],
    ) -> None:
        request = _resolve_request(options)
        _validate_archive(self, request)
        center = _resolve_processing_center(request)
        result = convert_sap_ish_zip_to_preanonymized_drop(
            zip_path=request.zip_path,
            output_dir=request.output_dir,
            source_system=request.source_system,
            center_name=request.center_name,
            center_key=request.center_key,
        )
        _write_import_summary(self, result)
        _process_generated_files(
            self,
            request=request,
            result=result,
            center=center,
        )
        _write_manifest(self, request=request, result=result)

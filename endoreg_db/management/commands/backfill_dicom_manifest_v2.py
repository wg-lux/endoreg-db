from __future__ import annotations

from dataclasses import asdict
import json

from django.core.management.base import BaseCommand, CommandError, CommandParser

from endoreg_db.management.command_errors import interoperability_command_error
from endoreg_db.services.interoperability.dicom_manifest_backfill import (
    DicomManifestBackfillError,
    backfill_dicom_export_manifests_v2,
)


class Command(BaseCommand):
    help = "Validate persisted DICOM manifests and optionally canonicalize them as V2."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply the transactional backfill; the default is dry-run.",
        )

    def handle(self, *args: object, **options: object) -> None:
        apply = options.get("apply")
        if not isinstance(apply, bool):
            raise CommandError("--apply must resolve to a boolean option")
        try:
            result = backfill_dicom_export_manifests_v2(apply=apply)
        except DicomManifestBackfillError as exc:
            raise interoperability_command_error(
                exc,
                command_name="backfill_dicom_manifest_v2",
            ) from exc
        self.stdout.write(json.dumps(asdict(result), sort_keys=True))

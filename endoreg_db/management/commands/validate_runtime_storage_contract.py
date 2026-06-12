from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError, CommandParser
from pydantic import ValidationError

from endoreg_db.utils.paths import (
    DATA_DIR,
    EXPORT_DIR,
    IMPORT_DIR,
    INGEST_PREANONYMIZED_DIR,
    INGEST_UPLOADS_DIR,
    LOG_DIR,
    MANAGED_ANONYMIZED_REPORTS_DIR,
    MANAGED_ANONYMIZED_VIDEOS_DIR,
    MANAGED_SENSITIVE_SIDECARS_DIR,
    MIGRATION_STAGING_DIR,
    PROTECTED_DATA_ROOT,
    QUARANTINE_DIR,
    QUARANTINE_FAILED_DIR,
    SAP_IMPORT_DROP_DIR,
    STORAGE_DIR,
    STAGING_MIGRATION_DIR,
    WATCHER_PREANONYMIZED_DROP_DIR,
    WATCHER_REPORT_DROP_DIR,
    WATCHER_VIDEO_DROP_DIR,
    ensure_within_data_root,
    ensure_within_protected_root,
)
from lx_dtypes.models.contracts.management_command import (
    RuntimeStorageContractPayload,
    ValidateRuntimeStorageContractCommandOptionsPayload,
)


class Command(BaseCommand):
    help = (
        "Validate that runtime storage paths resolve inside "
        "LX_ANNOTATE_ENCRYPTED_DATA_DIR and print the active contract."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit the runtime storage contract as JSON.",
        )

    def handle(self, *args: object, **options: object) -> None:
        try:
            options_payload = (
                ValidateRuntimeStorageContractCommandOptionsPayload.model_validate(
                    options
                )
            )
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc

        protected_paths: dict[str, Path] = {
            "protected_root": PROTECTED_DATA_ROOT,
            "storage": STORAGE_DIR,
            "ingest_uploads": INGEST_UPLOADS_DIR,
            "managed_anonymized_videos": MANAGED_ANONYMIZED_VIDEOS_DIR,
            "managed_anonymized_reports": MANAGED_ANONYMIZED_REPORTS_DIR,
            "managed_sensitive_sidecars": MANAGED_SENSITIVE_SIDECARS_DIR,
        }
        public_paths: dict[str, Path] = {
            "data_root": DATA_DIR,
            "import": IMPORT_DIR,
            "export": EXPORT_DIR,
            "ingest_preanonymized": INGEST_PREANONYMIZED_DIR,
            "logs": LOG_DIR,
            "quarantine": QUARANTINE_DIR,
            "quarantine_failed": QUARANTINE_FAILED_DIR,
            "migration_staging": MIGRATION_STAGING_DIR,
            "staging_migration": STAGING_MIGRATION_DIR,
            "watcher_video_drop": WATCHER_VIDEO_DROP_DIR,
            "watcher_report_drop": WATCHER_REPORT_DROP_DIR,
            "watcher_preanonymized_drop": WATCHER_PREANONYMIZED_DROP_DIR,
            "sap_import_drop": SAP_IMPORT_DROP_DIR,
        }

        violations: list[str] = []
        for label, path in protected_paths.items():
            try:
                ensure_within_protected_root(path)
            except ValueError as exc:
                violations.append(f"{label}: {exc}")

        for label, path in public_paths.items():
            try:
                ensure_within_data_root(path)
            except ValueError as exc:
                violations.append(f"{label}: {exc}")

        payload = RuntimeStorageContractPayload(
            protected_root=str(PROTECTED_DATA_ROOT),
            data_root=str(DATA_DIR),
            protected_paths={
                label: str(path) for label, path in protected_paths.items()
            },
            public_paths={label: str(path) for label, path in public_paths.items()},
            valid=not violations,
            violations=violations,
        )

        if options_payload.json:
            self.stdout.write(
                json.dumps(payload.model_dump(mode="json"), indent=2, sort_keys=True)
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f"Protected runtime root: {PROTECTED_DATA_ROOT}")
            )
            self.stdout.write(self.style.SUCCESS(f"Public data root: {DATA_DIR}"))
            for label, path in protected_paths.items():
                self.stdout.write(f"- {label}: {path}")
            for label, path in public_paths.items():
                self.stdout.write(f"- {label}: {path}")
            if violations:
                for violation in violations:
                    self.stdout.write(self.style.ERROR(f"! {violation}"))

        if violations:
            raise CommandError(
                "Runtime storage contract is invalid; one or more paths escape "
                "their configured protected or public runtime root."
            )

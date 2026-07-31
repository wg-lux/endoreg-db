from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

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

PathValidator = Callable[[str | Path], Path]


def _protected_paths() -> dict[str, Path]:
    return {
        "protected_root": PROTECTED_DATA_ROOT,
        "storage": STORAGE_DIR,
        "ingest_uploads": INGEST_UPLOADS_DIR,
        "managed_anonymized_videos": MANAGED_ANONYMIZED_VIDEOS_DIR,
        "managed_anonymized_reports": MANAGED_ANONYMIZED_REPORTS_DIR,
        "managed_sensitive_sidecars": MANAGED_SENSITIVE_SIDECARS_DIR,
    }


def _public_paths() -> dict[str, Path]:
    return {
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


def _collect_violations(
    paths: dict[str, Path],
    validator: PathValidator,
) -> list[str]:
    violations: list[str] = []
    for label, path in paths.items():
        try:
            validator(path)
        except ValueError as exc:
            violations.append(f"{label}: {exc}")
    return violations


def _build_payload(
    protected_paths: dict[str, Path],
    public_paths: dict[str, Path],
    violations: list[str],
) -> RuntimeStorageContractPayload:
    return RuntimeStorageContractPayload(
        protected_root=str(PROTECTED_DATA_ROOT),
        data_root=str(DATA_DIR),
        protected_paths={label: str(path) for label, path in protected_paths.items()},
        public_paths={label: str(path) for label, path in public_paths.items()},
        valid=not violations,
        violations=violations,
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
        options_payload = self._parse_options(options)
        protected_paths = _protected_paths()
        public_paths = _public_paths()
        violations = [
            *_collect_violations(protected_paths, ensure_within_protected_root),
            *_collect_violations(public_paths, ensure_within_data_root),
        ]
        payload = _build_payload(protected_paths, public_paths, violations)
        self._write_contract(
            payload,
            protected_paths=protected_paths,
            public_paths=public_paths,
            json_output=options_payload.json_output,
        )
        if violations:
            raise CommandError(
                "Runtime storage contract is invalid; one or more paths escape "
                "their configured protected or public runtime root."
            )

    @staticmethod
    def _parse_options(
        options: dict[str, object],
    ) -> ValidateRuntimeStorageContractCommandOptionsPayload:
        try:
            return (
                ValidateRuntimeStorageContractCommandOptionsPayload.model_validate(
                    options
                )
            )
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc

    def _write_contract(
        self,
        payload: RuntimeStorageContractPayload,
        *,
        protected_paths: dict[str, Path],
        public_paths: dict[str, Path],
        json_output: bool,
    ) -> None:
        if json_output:
            self.stdout.write(
                json.dumps(payload.model_dump(mode="json"), indent=2, sort_keys=True)
            )
            return
        self._write_text_contract(
            protected_paths,
            public_paths,
            violations=payload.violations,
        )

    def _write_text_contract(
        self,
        protected_paths: dict[str, Path],
        public_paths: dict[str, Path],
        *,
        violations: list[str],
    ) -> None:
        self.stdout.write(
            self.style.SUCCESS(f"Protected runtime root: {PROTECTED_DATA_ROOT}")
        )
        self.stdout.write(self.style.SUCCESS(f"Public data root: {DATA_DIR}"))
        for label, path in protected_paths.items():
            self.stdout.write(f"- {label}: {path}")
        for label, path in public_paths.items():
            self.stdout.write(f"- {label}: {path}")
        for violation in violations:
            self.stdout.write(self.style.ERROR(f"! {violation}"))

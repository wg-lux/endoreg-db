from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError, CommandParser

from endoreg_db.services.clinical_lifecycle import ClinicalRecordKind
from endoreg_db.services.clinical_lifecycle.audit import audit_clinical_records


class Command(BaseCommand):
    help = (
        "Project every video and report onto the end-to-end clinical lifecycle "
        "and report stage counts and invariant violations (read-only)."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--kind",
            choices=[kind.value for kind in ClinicalRecordKind],
            action="append",
            help="Restrict the audit to one record kind; repeatable.",
        )
        parser.add_argument(
            "--fail-on-violation",
            action="store_true",
            help="Exit non-zero when any record violates a lifecycle invariant.",
        )

    def handle(self, *args: object, **options: object) -> None:
        raw_kinds = options.get("kind")
        if raw_kinds is None:
            kinds = list(ClinicalRecordKind)
        elif isinstance(raw_kinds, list):
            kinds = [ClinicalRecordKind(str(value)) for value in raw_kinds]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        else:
            raise CommandError(f"invalid --kind option: {raw_kinds!r}")

        audit = audit_clinical_records(kinds)
        self.stdout.write(json.dumps(audit.to_json(), sort_keys=True, indent=2))
        if options.get("fail_on_violation") and audit.violations:
            raise CommandError(
                f"{len(audit.violations)} records violate clinical lifecycle invariants."
            )

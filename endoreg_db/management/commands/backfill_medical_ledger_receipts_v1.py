from __future__ import annotations

from dataclasses import asdict
import json

from django.core.management.base import BaseCommand, CommandError, CommandParser

from endoreg_db.services.medical_ledger import (
    MedicalLedgerReceiptBackfillError,
    backfill_medical_ledger_receipts_v1,
)


class Command(BaseCommand):
    help = (
        "Validate persisted medical-ledger receipts and optionally "
        "canonicalize them as schema version 1.0."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply the atomic backfill; the default is dry-run.",
        )

    def handle(self, *args: object, **options: object) -> None:
        apply = options.get("apply")
        if not isinstance(apply, bool):
            raise CommandError("--apply must resolve to a boolean option")
        try:
            result = backfill_medical_ledger_receipts_v1(apply=apply)
        except MedicalLedgerReceiptBackfillError as exc:
            raise CommandError(
                "medical_ledger_receipt_invalid: "
                f"{exc.model_label} receipt_id={exc.receipt_id} "
                f"schema_version={exc.observed_version!r} "
                f"reason={exc.reason}"
            ) from exc
        self.stdout.write(json.dumps(asdict(result), sort_keys=True))

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from endoreg_db.services.audit_integrity import (
    refresh_audit_ledger_integrity_status,
    refresh_audit_ledger_integrity_status_once,
)


class Command(BaseCommand):
    help = "Refresh and print audit ledger integrity status."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--once",
            action="store_true",
            help="Use lock-aware single-run refresh semantics.",
        )
        parser.add_argument(
            "--pretty",
            action="store_true",
            help="Pretty-print JSON output.",
        )
        parser.add_argument(
            "--fail-on-non-verified",
            action="store_true",
            help=(
                "Exit non-zero unless status is verified "
                "(useful for operational recovery gates)."
            ),
        )

    def handle(self, *args, **options) -> None:
        if options["once"]:
            payload = refresh_audit_ledger_integrity_status_once()
        else:
            payload = refresh_audit_ledger_integrity_status()

        self.stdout.write(
            json.dumps(
                payload,
                sort_keys=True,
                indent=2 if options["pretty"] else None,
            )
        )

        if options["fail_on_non_verified"] and payload.get("status") != "verified":
            raise CommandError(
                "Audit ledger integrity is not verified: "
                f"status={payload.get('status')} error={payload.get('error')}"
            )

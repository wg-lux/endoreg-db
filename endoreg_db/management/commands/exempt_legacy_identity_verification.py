from __future__ import annotations

import json
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone

from endoreg_db.services.clinical_lifecycle.legacy_exemption import (
    legacy_identity_candidates,
    record_legacy_identity_exemptions,
)


def _parse_cutoff(value: str) -> datetime:
    try:
        cutoff = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CommandError(f"--cutoff must be an ISO 8601 timestamp: {exc}") from exc
    if timezone.is_naive(cutoff):
        raise CommandError("--cutoff must include a timezone offset")
    return cutoff


class Command(BaseCommand):
    help = (
        "Record a named exception for videos validated before identity "
        "verification was required. Dry run unless --apply is given. The "
        "exemption never authorizes new validations and never waives the purge "
        "of raw media and direct identifiers before export."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--cutoff",
            required=True,
            help="Only videos imported before this ISO 8601 timestamp qualify.",
        )
        parser.add_argument("--actor", help="Operator recorded in the ledger.")
        parser.add_argument(
            "--reason", help="Justification recorded in the ledger; no patient data."
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write the exemptions; otherwise only list candidates.",
        )

    def handle(self, *args: object, **options: object) -> None:
        cutoff = _parse_cutoff(str(options["cutoff"]))
        try:
            candidates = legacy_identity_candidates(cutoff)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        written = 0
        if options.get("apply"):
            actor = str(options.get("actor") or "").strip()
            reason = str(options.get("reason") or "").strip()
            if not actor or not reason:
                raise CommandError("--apply requires --actor and --reason")
            written = record_legacy_identity_exemptions(
                candidates, cutoff=cutoff, actor=actor, reason=reason
            )

        self.stdout.write(
            json.dumps(
                {
                    "applied": bool(options.get("apply")),
                    "cutoff": cutoff.isoformat(),
                    "candidate_count": len(candidates),
                    "candidate_video_ids": [c.video_id for c in candidates],
                    "written": written,
                },
                sort_keys=True,
                indent=2,
            )
        )

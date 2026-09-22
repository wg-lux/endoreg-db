from __future__ import annotations

from datetime import datetime
from typing import cast

from django.core.management.base import BaseCommand, CommandError, CommandParser
from endoreg_db.services.hls_legacy_adoption import adopt_legacy_hls


class Command(BaseCommand):
    help = "Adopt explicitly confirmed unchanged legacy HLS without re-encoding; dry-run by default."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--artifact-id", type=int, action="append", required=True)
        parser.add_argument(
            "--created-before", type=datetime.fromisoformat, required=True
        )
        parser.add_argument("--approved-by", required=True)
        parser.add_argument("--reason", required=True)
        parser.add_argument("--accept-unchanged-source", action="store_true")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args: object, **options: object) -> None:
        del args
        for artifact_id in cast(list[int], options["artifact_id"]):
            try:
                receipt = adopt_legacy_hls(
                    artifact_id=artifact_id,
                    approved_by=cast(str, options["approved_by"]),
                    reason=cast(str, options["reason"]),
                    created_before=cast(datetime, options["created_before"]),
                    accept_unchanged_source=bool(options["accept_unchanged_source"]),
                    apply=bool(options["apply"]),
                )
            except (OSError, ValueError, RuntimeError) as exc:
                raise CommandError(
                    f"Artifact {artifact_id}: {type(exc).__name__}: {exc}"
                ) from exc
            # Full before/after identity stays in the protected receipt directory.
            self.stdout.write(
                f"artifact={artifact_id} status={receipt.status} receipt={receipt.receipt_id}"
            )

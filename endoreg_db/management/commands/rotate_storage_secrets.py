from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError, CommandParser

from endoreg_db.services.secret_rotation.storage import rotate_storage


class Command(BaseCommand):
    help = "Authenticate or rotate registered encrypted files using the private online keyring."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Re-encrypt with fresh data keys; otherwise authenticate only.",
        )
        parser.add_argument(
            "--include-hls",
            action="store_true",
            help="Also regenerate ready streaming generations using fresh content keys.",
        )

    def handle(self, *args: object, **options: object) -> None:
        try:
            report = rotate_storage(
                apply=options.get("apply") is True,
                include_hls=options.get("include_hls") is True,
            )
        except (OSError, ValueError):
            raise CommandError(
                "Rotation configuration or inventory failed; no secrets are included in diagnostics"
            ) from None
        self.stdout.write(json.dumps(report.counts(), sort_keys=True))
        if (
            report.failed
            or report.deferred
            or report.unmanaged_encrypted
            or report.staging_files_pending_review
            or report.missing_registered_files
        ):
            raise CommandError(
                "Rotation is incomplete; resolve failures/unmanaged files and rerun after leases expire"
            )
        self.stdout.write(
            "File verification complete. HLS generations, backups and replicas require separate retirement checks."
        )

from __future__ import annotations

from django.core.management.base import BaseCommand

from endoreg_db.services.hub.cleanup import reap_upload_job_sources


class Command(BaseCommand):
    help = "Delete persisted UploadJob source files that are already marked cleanup-eligible."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Maximum number of eligible upload job sources to clean in one batch.",
        )
        parser.add_argument(
            "--repeat-until-empty",
            action="store_true",
            help="Keep reaping in batches until no eligible sources remain.",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        repeat_until_empty = bool(options["repeat_until_empty"])

        total_cleaned = 0
        while True:
            cleaned = reap_upload_job_sources(limit=limit)
            total_cleaned += cleaned
            self.stdout.write(f"cleaned={cleaned} total_cleaned={total_cleaned}")
            if not repeat_until_empty or cleaned == 0:
                break

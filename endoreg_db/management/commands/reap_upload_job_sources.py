from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError, CommandParser
from pydantic import ValidationError

from endoreg_db.services.hub.cleanup import reap_upload_job_sources
from lx_dtypes.models.contracts.management_command import (
    ReapUploadJobSourcesCommandOptionsPayload,
)


class Command(BaseCommand):
    help = "Delete persisted UploadJob source files that are already marked cleanup-eligible."

    def add_arguments(self, parser: CommandParser) -> None:
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

    def handle(self, *args: object, **options: object) -> None:
        try:
            command_options = ReapUploadJobSourcesCommandOptionsPayload.model_validate(
                options
            )
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc

        limit = command_options.limit
        repeat_until_empty = command_options.repeat_until_empty

        total_cleaned = 0
        while True:
            if limit > 0:
                cleaned = reap_upload_job_sources(limit=limit)
            else:
                cleaned = reap_upload_job_sources()
            total_cleaned += cleaned
            self.stdout.write(f"cleaned={cleaned} total_cleaned={total_cleaned}")
            if not repeat_until_empty or cleaned == 0:
                break

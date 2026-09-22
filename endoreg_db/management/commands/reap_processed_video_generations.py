from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError

from endoreg_db.schemas.processed_video_cleanup import ReapProcessedGenerationOptions
from endoreg_db.services.processed_video_cleanup import (
    cleanup_processed_video_generations,
)


class Command(BaseCommand):
    help = "Retry recorded processed-video and HTTP Live Streaming generation cleanup."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--video-id", type=int, required=True)
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Delete eligible generations; default is dry-run.",
        )

    def handle(self, *args: object, **options: object) -> None:
        try:
            parsed = ReapProcessedGenerationOptions.model_validate(options)
            result = cleanup_processed_video_generations(
                parsed.video_id, apply=parsed.apply
            )
        except (
            OSError,
            ValueError,
            RuntimeError,
            DatabaseError,
            ObjectDoesNotExist,
        ) as exc:
            raise CommandError(
                f"Processed generation cleanup failed ({type(exc).__name__}); receipt retained."
            ) from exc
        self.stdout.write(result.model_dump_json())

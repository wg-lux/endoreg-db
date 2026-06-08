from __future__ import annotations

from typing import TypedDict, Unpack

from django.core.management.base import BaseCommand, CommandError, CommandParser
from lx_dtypes.models.contracts.management_command import (
    MigrateVideoStreamableStorageCommandOptionsPayload,
)

from endoreg_db.models import VideoFile
from endoreg_db.services.streamable_media import sync_video_streamable_artifacts


class MigrateVideoStreamableStorageCommandOptions(TypedDict):
    video_ids: list[int] | None
    processed_only: bool
    raw_only: bool
    dry_run: bool


class Command(BaseCommand):
    help = (
        "Materialize streamable protected-media copies for existing videos and "
        "stamp VideoFile storage metadata for X-Accel-Redirect delivery."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--video-id",
            type=int,
            action="append",
            dest="video_ids",
            help="Restrict migration to one or more specific VideoFile IDs.",
        )
        parser.add_argument(
            "--processed-only",
            action="store_true",
            help="Only synchronize processed video artifacts.",
        )
        parser.add_argument(
            "--raw-only",
            action="store_true",
            help="Only synchronize raw video artifacts.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without copying files or saving metadata.",
        )

    def handle(
        self,
        *args: str,
        **options: Unpack[MigrateVideoStreamableStorageCommandOptions],
    ) -> None:
        options_payload = (
            MigrateVideoStreamableStorageCommandOptionsPayload.model_validate(options)
        )
        video_ids = options_payload.video_ids
        processed_only = options_payload.processed_only
        raw_only = options_payload.raw_only
        if processed_only and raw_only:
            raise CommandError(
                "--processed-only and --raw-only cannot be used together"
            )
        dry_run = options_payload.dry_run

        include_raw = not processed_only
        include_processed = not raw_only

        queryset = VideoFile.objects.all().order_by("pk")
        if video_ids:
            queryset = queryset.filter(pk__in=video_ids)

        migrated = 0
        unchanged = 0
        failed = 0

        for video in queryset.iterator():
            try:
                update_fields = sync_video_streamable_artifacts(
                    video,
                    include_raw=include_raw,
                    include_processed=include_processed,
                    save=not dry_run,
                )
            except Exception as exc:
                failed += 1
                self.stderr.write(
                    self.style.ERROR(
                        f"video={video.pk} failed to synchronize streamable media: {exc}"
                    )
                )
                continue

            if update_fields:
                migrated += 1
                action = "would update" if dry_run else "updated"
                self.stdout.write(
                    self.style.SUCCESS(
                        f"video={video.pk} {action}: {', '.join(update_fields)}"
                    )
                )
            else:
                unchanged += 1
                self.stdout.write(f"video={video.pk} unchanged")

        summary = (
            f"streamable video migration complete: migrated={migrated} "
            f"unchanged={unchanged} failed={failed}"
        )
        if failed:
            self.stderr.write(self.style.WARNING(summary))
        else:
            self.stdout.write(self.style.SUCCESS(summary))

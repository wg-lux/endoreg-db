from __future__ import annotations

from typing import TypedDict, Unpack

from django.core.management.base import BaseCommand, CommandError, CommandParser
from lx_dtypes.models.contracts.management_command import (
    MigrateVideoStreamableStorageCommandOptionsPayload,
)

from endoreg_db.models import VideoFile
from endoreg_db.services.streamable_media import (
    StreamableArtifactDisposition,
    resolve_streamable_media_state,
    sync_video_streamable_artifacts,
)


class MigrateVideoStreamableStorageCommandOptions(TypedDict):
    video_ids: list[int] | None
    processed_only: bool
    raw_only: bool
    dry_run: bool
    regenerate: bool


def _selected_streamable_artifact_count(
    video: VideoFile,
    *,
    include_raw: bool,
    include_processed: bool,
) -> int:
    state = resolve_streamable_media_state(
        video,
        include_raw=include_raw,
        include_processed=include_processed,
    )
    return sum(
        1
        for decision in state.artifacts
        if decision.disposition == StreamableArtifactDisposition.SYNC
    )


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
        parser.add_argument(
            "--regenerate",
            "--force",
            action="store_true",
            dest="regenerate",
            help=(
                "Rewrite selected streamable artifacts even when an existing "
                "proxy already looks valid."
            ),
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
        regenerate = bool(options.get("regenerate", False))

        include_raw = not processed_only
        include_processed = not raw_only

        queryset = VideoFile.objects.all().order_by("pk")
        if video_ids:
            queryset = queryset.filter(pk__in=video_ids)

        migrated = 0
        regenerated = 0
        unchanged = 0
        failed = 0

        for video in queryset.iterator():
            try:
                selected_streamable_count = (
                    _selected_streamable_artifact_count(
                        video,
                        include_raw=include_raw,
                        include_processed=include_processed,
                    )
                    if regenerate
                    else 0
                )
                update_fields = sync_video_streamable_artifacts(
                    video,
                    include_raw=include_raw,
                    include_processed=include_processed,
                    save=not dry_run,
                    force=regenerate,
                )
            except Exception as exc:
                failed += 1
                self.stderr.write(
                    self.style.ERROR(
                        f"video={video.pk} failed to synchronize streamable media: {exc}"
                    )
                )
                continue

            if selected_streamable_count:
                regenerated += 1

            if update_fields:
                migrated += 1
                action = "would update" if dry_run else "updated"
                self.stdout.write(
                    self.style.SUCCESS(
                        f"video={video.pk} {action}: {', '.join(update_fields)}"
                    )
                )
            elif selected_streamable_count:
                action = "would regenerate" if dry_run else "regenerated"
                self.stdout.write(
                    self.style.SUCCESS(
                        f"video={video.pk} {action}: "
                        f"{selected_streamable_count} streamable artifact(s)"
                    )
                )
            else:
                unchanged += 1
                self.stdout.write(f"video={video.pk} unchanged")

        summary = (
            f"streamable video migration complete: migrated={migrated} "
            f"regenerated={regenerated} "
            f"unchanged={unchanged} failed={failed}"
        )
        if failed:
            self.stderr.write(self.style.WARNING(summary))
        else:
            self.stdout.write(self.style.SUCCESS(summary))

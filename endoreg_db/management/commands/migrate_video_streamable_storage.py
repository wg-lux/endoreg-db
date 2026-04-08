from __future__ import annotations

from django.core.management.base import BaseCommand

from endoreg_db.models import VideoFile
from endoreg_db.services.streamable_media import sync_video_streamable_artifacts


class Command(BaseCommand):
    help = (
        "Materialize streamable protected-media copies for existing videos and "
        "stamp VideoFile storage metadata for X-Accel-Redirect delivery."
    )

    def add_arguments(self, parser) -> None:
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

    def handle(self, *args, **options) -> None:
        video_ids = options.get("video_ids") or []
        processed_only = bool(options["processed_only"])
        raw_only = bool(options["raw_only"])
        dry_run = bool(options["dry_run"])

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

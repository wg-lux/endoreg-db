from __future__ import annotations

from typing import Sequence

from django.core.management.base import BaseCommand, CommandError

from endoreg_db.models import VideoFile
from endoreg_db.services.segment_annotations import ensure_segment_annotations


class Command(BaseCommand):
    help = (
        "Populate ImageClassificationAnnotation rows for LabelVideoSegments that "
        "currently have no annotations so that export pipelines see the segment labels."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--video-id",
            dest="video_ids",
            type=int,
            action="append",
            help="Optional video ID to limit the migration. Can be passed multiple times.",
        )
        parser.add_argument(
            "--segment-id",
            dest="segment_ids",
            type=int,
            action="append",
            help="Optional segment ID to limit the migration. Can be passed multiple times.",
        )
        parser.add_argument(
            "--all-videos",
            action="store_true",
            dest="all_videos",
            help="Process every video in the database.",
        )
        parser.add_argument(
            "--information-source-name",
            dest="information_source_name",
            type=str,
            default="manual_annotation",
            help="InformationSource.name to attach to the inserted annotations.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            dest="dry_run",
            help="Report how many annotations would be created without inserting rows.",
        )

    def handle(self, *args, **options):
        video_ids: Sequence[int] | None = options.get("video_ids")
        segment_ids: Sequence[int] | None = options.get("segment_ids")
        all_videos: bool = options.get("all_videos", False)

        if not all_videos and not video_ids and not segment_ids:
            raise CommandError(
                "Specify --video-id (once or multiple times), --segment-id, or --all-videos."
            )

        if all_videos:
            video_ids = list(VideoFile.objects.values_list("id", flat=True))

        commit = not options.get("dry_run", False)

        summary = ensure_segment_annotations(
            video_ids=video_ids,
            segment_ids=segment_ids,
            information_source_name=options["information_source_name"],
            commit=commit,
        )

        if options.get("dry_run"):
            self.stdout.write(
                self.style.NOTICE(
                    "Dry run: missing annotations would have been created for "
                    f"{summary['annotations_needed']} frames across "
                    f"{summary['segments_processed']} segments."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "Migration finished: "
                    f"created {summary['annotations_created']} annotations for "
                    f"{summary['segments_processed']} segments."
                )
            )

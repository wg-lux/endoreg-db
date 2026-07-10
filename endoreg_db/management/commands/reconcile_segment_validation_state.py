from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError, CommandParser
from pydantic import ValidationError

from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.state.video_segment_validation import (
    resolve_segment_annotation_status,
)
from endoreg_db.services.jobs.video_post_validation_jobs import (
    dispatch_video_post_validation_rebuild,
)
from lx_dtypes.models.contracts.management_command import (
    ReconcileSegmentValidationStateCommandOptionsPayload,
)


class Command(BaseCommand):
    help = (
        "Find legacy videos where segment_annotations_validated was set before "
        "outside-frame cleanup completed."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--video-id",
            dest="video_ids",
            type=int,
            action="append",
            help="Optional video ID to inspect. Can be passed multiple times.",
        )
        parser.add_argument(
            "--queue-cleanup",
            action="store_true",
            dest="queue_cleanup",
            help="Queue post-validation cleanup for matching videos.",
        )

    def handle(self, *args: object, **options: object) -> None:
        try:
            command_options = (
                ReconcileSegmentValidationStateCommandOptionsPayload.model_validate(
                    options
                )
            )
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc

        queryset = VideoFile.objects.select_related("state").filter(
            state__segment_annotations_validated=True,
            state__outside_segments_removed=False,
        )
        video_ids = command_options.video_ids
        if video_ids:
            queryset = queryset.filter(pk__in=video_ids)

        videos = list(queryset.order_by("pk"))
        if not videos:
            self.stdout.write(
                self.style.SUCCESS("No premature validation states found.")
            )
            return

        queue_cleanup = command_options.queue_cleanup
        self.stdout.write(
            self.style.WARNING(
                f"Found {len(videos)} video(s) with premature segment validation."
            )
        )
        for video in videos:
            status = resolve_segment_annotation_status(video)
            if queue_cleanup:
                job = dispatch_video_post_validation_rebuild(video_id=video.pk)
                self.stdout.write(
                    f"video_id={video.pk} status={status} queued_status={job.status} "
                    f"history_id={job.history_id}"
                )
            else:
                self.stdout.write(f"video_id={video.pk} status={status} action=dry_run")

        if not queue_cleanup:
            self.stdout.write(
                self.style.NOTICE(
                    "Dry run only. Re-run with --queue-cleanup to dispatch cleanup jobs."
                )
            )

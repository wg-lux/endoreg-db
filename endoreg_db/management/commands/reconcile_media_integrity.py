from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError, CommandParser
from pydantic import ValidationError

from endoreg_db.services.media_integrity import reconcile_media_integrity
from lx_dtypes.models.contracts.management_command import (
    ReconcileMediaIntegrityCommandOptionsPayload,
)


class Command(BaseCommand):
    help = (
        "Reconcile media metadata against on-disk state, repair conservative "
        "discrepancies, and mark unrecoverable records as LOST."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report planned reconciliation actions without mutating database or files.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Write the reconciliation summary as JSON.",
        )
        parser.add_argument(
            "--video-id",
            action="append",
            type=int,
            default=[],
            help="Limit reconciliation to a video id. May be provided multiple times.",
        )
        parser.add_argument(
            "--check-frames",
            action="store_true",
            help="Classify frame DB/cache integrity for each selected video.",
        )
        parser.add_argument(
            "--repair-frames",
            action="store_true",
            help="Repair explicitly safe frame cache issues.",
        )
        parser.add_argument(
            "--repair-frame",
            action="append",
            type=int,
            default=[],
            help="Explicitly repair one frame number, even when the cache is missing. May be provided multiple times.",
        )
        parser.add_argument(
            "--check-ffmpeg-meta",
            action="store_true",
            help="Probe and report ffmpeg metadata/FPS provenance.",
        )
        parser.add_argument(
            "--repair-ffmpeg-meta",
            action="store_true",
            help="Backfill missing ffmpeg metadata from an explicit probe source.",
        )
        parser.add_argument(
            "--check-streamable-probe",
            action="store_true",
            help="Run ffprobe against streamable artifacts and verify canonical media before repair.",
        )
        parser.add_argument(
            "--cleanup-stale-artifacts",
            action="store_true",
            help="Remove stale temporary media artifacts through the existing reconciliation cleanup.",
        )

    def handle(self, *args: object, **options: object) -> None:
        try:
            command_options = ReconcileMediaIntegrityCommandOptionsPayload.model_validate(
                options
            )
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc

        summary = reconcile_media_integrity(
            dry_run=command_options.dry_run,
            video_ids=command_options.video_id,
            check_frames=command_options.check_frames
            or command_options.repair_frames,
            repair_frames=command_options.repair_frames,
            repair_frame_numbers=command_options.repair_frame,
            check_ffmpeg_meta=command_options.check_ffmpeg_meta
            or command_options.repair_ffmpeg_meta,
            repair_ffmpeg_meta=command_options.repair_ffmpeg_meta,
            check_streamable_probe=command_options.check_streamable_probe,
            cleanup_stale_artifacts=command_options.cleanup_stale_artifacts,
        )
        summary.dry_run = command_options.dry_run
        if command_options.json:
            self.stdout.write(json.dumps(summary.as_dict(), indent=2, sort_keys=True))
            return
        self.stdout.write(
            self.style.SUCCESS(
                "media integrity reconciliation complete: "
                f"videos={summary.checked_videos} "
                f"upload_jobs={summary.checked_upload_jobs} "
                f"repaired={summary.repaired_records} "
                f"lost={summary.lost_records}"
            )
        )

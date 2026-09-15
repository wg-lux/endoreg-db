from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError, CommandParser
from pydantic import ValidationError

from endoreg_db.management.commands._profiling import (
    add_profiling_arguments,
    command_profiling_config_from_options,
    run_with_optional_profile,
)
from endoreg_db.services.frame_segment_reconciliation import (
    FrameSegmentReconciliationSpec,
    VALID_FRAME_SEGMENT_TRACKS,
    reconcile_frame_segment_annotations,
)
from lx_dtypes.models.contracts.management_command import (
    ReconcileFrameSegmentAnnotationsCommandOptionsPayload,
)


class Command(BaseCommand):
    help = (
        "Reconcile LabelVideoSegment rows with segment-derived "
        "ImageClassificationAnnotation rows."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--video-id",
            dest="video_ids",
            type=int,
            action="append",
            default=[],
            help="Restrict reconciliation to a video id. May be provided multiple times.",
        )
        parser.add_argument(
            "--segment-id",
            dest="segment_ids",
            type=int,
            action="append",
            default=[],
            help="Restrict reconciliation to a segment id. May be provided multiple times.",
        )
        parser.add_argument(
            "--annotator",
            type=str,
            default=None,
            help="Restrict reconciliation to one annotator track.",
        )
        parser.add_argument(
            "--track",
            choices=sorted(VALID_FRAME_SEGMENT_TRACKS),
            default="all",
            help="Restrict reconciliation to manual, prediction, or all tracks.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            dest="apply_changes",
            help="Create missing generated annotations and delete stale generated annotations.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Write the reconciliation report as JSON.",
        )
        add_profiling_arguments(parser)

    def handle(self, *args: object, **options: object) -> None:
        profiling_config = command_profiling_config_from_options(options)
        return run_with_optional_profile(
            lambda: self._handle_unprofiled(*args, **options),
            config=profiling_config,
        )

    def _handle_unprofiled(self, *args: object, **options: object) -> None:
        _ = args
        try:
            command_options = (
                ReconcileFrameSegmentAnnotationsCommandOptionsPayload.model_validate(
                    options
                )
            )
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc

        video_ids = command_options.video_ids
        segment_ids = command_options.segment_ids
        apply_changes = command_options.apply_changes
        if apply_changes and not video_ids and not segment_ids:
            raise CommandError(
                "--apply requires at least one --video-id or --segment-id."
            )

        spec = FrameSegmentReconciliationSpec(
            video_ids=video_ids,
            segment_ids=segment_ids,
            annotator=command_options.annotator or None,
            track=command_options.track,
            apply=apply_changes,
        )
        report = reconcile_frame_segment_annotations(spec)
        payload = report.as_dict()
        if command_options.json_output:
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
            return

        summary = report.summary
        mode = "applied" if apply_changes else "dry-run"
        self.stdout.write(
            self.style.SUCCESS(
                "frame/segment annotation reconciliation complete "
                f"({mode}): segments={summary.eligible_segments} "
                f"expected={summary.expected_annotations} "
                f"missing={summary.missing_annotations} "
                f"created={summary.created_annotations} "
                f"stale_generated={summary.stale_generated_annotations} "
                f"deleted={summary.deleted_stale_generated_annotations} "
                f"suspicious_unmarked={summary.suspicious_unmarked_annotations}"
            )
        )

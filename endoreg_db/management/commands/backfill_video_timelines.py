from __future__ import annotations

import json
from typing import cast

from django.core.management.base import BaseCommand, CommandError, CommandParser

from endoreg_db.services.video_files.timeline_backfill import (
    backfill_video_timelines,
)


class Command(BaseCommand):
    help = (
        "Report videos whose presentation timeline is missing and, with --apply, "
        "recover it from hash-verified canonical media. Videos without verifiable "
        "media are listed with the reason and left unchanged."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Backfill recoverable videos; the default is a read-only report.",
        )
        parser.add_argument(
            "--video-id",
            action="append",
            type=int,
            dest="video_ids",
            help="Limit the run to this video; repeat for several.",
        )
        parser.add_argument(
            "--confirm-processed-ordinals",
            action="store_true",
            help="Explicitly confirm saved annotation ordinals belong to the published processed generation; preview unless --apply is given.",
        )
        parser.add_argument(
            "--operator",
            help="Identified operator responsible for processed ordinal confirmation.",
        )

    def handle(self, *args: object, **options: object) -> None:
        apply = options.get("apply")
        if not isinstance(apply, bool):
            raise CommandError("--apply must resolve to a boolean option")
        raw_video_ids = options.get("video_ids")
        video_ids: list[int] | None = None
        if raw_video_ids is not None:
            if not isinstance(raw_video_ids, list):
                raise CommandError("--video-id must be an integer")
            video_ids = []
            for video_id in cast(list[object], raw_video_ids):
                if not isinstance(video_id, int):
                    raise CommandError("--video-id must be an integer")
                video_ids.append(video_id)
        confirmed = options.get("confirm_processed_ordinals", False)
        operator = options.get("operator")
        if (
            not isinstance(confirmed, bool)
            or (
                confirmed
                and (
                    not video_ids
                    or not isinstance(operator, str)
                    or not operator.strip()
                )
            )
            or (not confirmed and operator is not None)
        ):
            raise CommandError(
                "Processed ordinal recovery requires --confirm-processed-ordinals, --operator and explicit --video-id"
            )
        try:
            result = backfill_video_timelines(
                apply=apply,
                video_ids=video_ids,
                processed_ordinals_operator=operator
                if isinstance(operator, str)
                else None,
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(result.to_dict(), sort_keys=True))
        if result.failed:
            raise CommandError(
                f"Timeline backfill failed for {result.failed} video(s); "
                "see the report above."
            )

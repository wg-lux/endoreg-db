from __future__ import annotations

import json
from typing import Any

from django.core.management.base import CommandError, CommandParser

from endoreg_db.models import VideoFile
from endoreg_db.services.video_processed_transcode import (
    ProcessedVideoTranscodeResult,
    summarize_processed_video_transcode_results,
    transcode_processed_video_for_storage_pressure,
)

from ._video_command_base import BaseVideoCommand


class Command(BaseVideoCommand):
    help = (
        "Transcode existing processed videos to smaller encrypted replacements, "
        "update processed hashes, regenerate streamable artifacts, and delete "
        "replaced versions after commit. Suitable for incremental systemd runs "
        "during storage pressure."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        self.add_video_selection_arguments(
            parser,
            limit_help="Maximum number of selected videos to process in this run.",
        )
        self.add_apply_argument(
            parser,
            help_text=(
                "Persist replacements. Without this flag the command is a dry run."
            ),
        )
        parser.add_argument(
            "--quality-mode",
            choices=("fast", "balanced", "quality"),
            default="balanced",
            help="Encoding quality preset passed to the FFmpeg wrapper.",
        )
        parser.add_argument(
            "--force-cpu",
            action="store_true",
            help="Force CPU H.264 encoding instead of automatic encoder selection.",
        )
        parser.add_argument(
            "--allow-larger",
            action="store_true",
            help="Allow replacement even when the transcoded output is not smaller.",
        )
        parser.add_argument(
            "--fail-on-skipped",
            action="store_true",
            help="Exit non-zero if any selected video is skipped.",
        )
        self.add_json_output_argument(parser)

    def handle(self, *args: object, **options: object) -> None:
        video_ids = self.selected_video_ids_from_options(options)
        limit = self.positive_limit_from_options(options)
        apply_changes = bool(options.get("apply"))
        quality_mode = str(options.get("quality_mode") or "balanced")
        force_cpu = bool(options.get("force_cpu"))
        allow_larger = bool(options.get("allow_larger"))
        fail_on_skipped = bool(options.get("fail_on_skipped"))
        json_output = bool(options.get("json_output"))

        queryset = (
            VideoFile.objects.exclude(processed_file="")
            .exclude(processed_file__isnull=True)
            .order_by("pk")
        )
        queryset = self.apply_video_selection(
            queryset,
            video_ids=video_ids,
            limit=limit,
        )

        results: list[ProcessedVideoTranscodeResult] = []
        for video in queryset.iterator():
            result = transcode_processed_video_for_storage_pressure(
                video,
                apply=apply_changes,
                quality_mode=quality_mode,
                force_cpu=force_cpu,
                allow_larger=allow_larger,
            )
            results.append(result)
            if not json_output:
                self._write_result(result)

        summary = summarize_processed_video_transcode_results(results)
        payload = {
            "apply": apply_changes,
            "summary": summary.as_dict(),
            "results": [self._result_payload(result) for result in results],
        }
        if json_output:
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
        else:
            style = self.style.WARNING if summary.failed else self.style.SUCCESS
            self.stdout.write(
                style(
                    "processed video transcode complete: "
                    f"selected={summary.selected} changed={summary.changed} "
                    f"dry_run={summary.dry_run} skipped={summary.skipped} "
                    f"failed={summary.failed}"
                )
            )

        if summary.failed:
            raise CommandError(
                f"Processed video transcode failed for {summary.failed} video(s)."
            )
        if fail_on_skipped and summary.skipped:
            raise CommandError(
                f"Processed video transcode skipped {summary.skipped} video(s)."
            )

    def _write_result(self, result: ProcessedVideoTranscodeResult) -> None:
        line = (
            f"video={result.video_id} status={result.status} "
            f"old_size={result.old_size} new_size={result.new_size}"
        )
        if result.detail:
            line = f"{line} detail={result.detail}"
        if result.status == "changed":
            self.stdout.write(self.style.SUCCESS(line))
        elif result.status == "failed":
            self.stderr.write(self.style.ERROR(line))
        else:
            self.stdout.write(line)

    @staticmethod
    def _result_payload(result: ProcessedVideoTranscodeResult) -> dict[str, Any]:
        return {
            "video_id": result.video_id,
            "status": result.status,
            "old_hash": result.old_hash,
            "new_hash": result.new_hash,
            "old_size": result.old_size,
            "new_size": result.new_size,
            "old_processed_name": result.old_processed_name,
            "new_processed_name": result.new_processed_name,
            "old_streamable_relative_path": result.old_streamable_relative_path,
            "new_streamable_relative_path": result.new_streamable_relative_path,
            "detail": result.detail,
        }

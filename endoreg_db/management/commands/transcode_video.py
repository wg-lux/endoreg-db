from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError, CommandParser
from pydantic import ValidationError

from endoreg_db.services.video_format_reconciliation import VIDEO_EXTENSIONS
from endoreg_db.services.video_transcoding import transcode_video_directory
from lx_dtypes.models.contracts.management_command import (
    TranscodeVideoCommandOptionsPayload,
)


class Command(BaseCommand):
    help = (
        "Transcode local video files from --input-dir into --output-dir using "
        "the configured system video standard."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--input-dir",
            required=True,
            help="Directory containing source video files.",
        )
        parser.add_argument(
            "--output-dir",
            required=True,
            help=(
                "Directory for standardized MP4 outputs. By default this must "
                "be inside the configured protected or data root."
            ),
        )
        parser.add_argument(
            "--filename",
            default=None,
            help=(
                "Optional source filename or relative path inside --input-dir. "
                "When omitted, all supported videos in the input directory are processed."
            ),
        )
        parser.add_argument(
            "--recursive",
            action="store_true",
            help="Scan input subdirectories and preserve their relative layout.",
        )
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Replace an existing destination file after successful staging.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report planned transcodes without creating output files.",
        )
        parser.add_argument(
            "--allow-unmanaged-output",
            action="store_true",
            help=(
                "Allow output outside configured protected/data roots. Intended "
                "only for one-off local operator runs."
            ),
        )
        parser.add_argument(
            "--force-cpu",
            action="store_true",
            help="Force CPU H.264 encoding instead of automatic encoder selection.",
        )
        parser.add_argument(
            "--quality-mode",
            choices=("fast", "balanced", "quality"),
            default="balanced",
            help="Encoding quality preset used by the system FFmpeg wrapper.",
        )
        parser.add_argument(
            "--extension",
            action="append",
            default=[],
            help=(
                "Video extension to include, for example .mp4. May be supplied "
                "multiple times. Defaults to common video extensions."
            ),
        )
        parser.add_argument(
            "--fail-on-skipped",
            action="store_true",
            help="Exit non-zero if any selected file is skipped.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit the summary as JSON.",
        )

    def handle(self, *args: object, **options: object) -> None:
        try:
            options_payload = TranscodeVideoCommandOptionsPayload.model_validate(
                options
            )
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc

        filename = options_payload.filename or None
        extensions = options_payload.extension or tuple(VIDEO_EXTENSIONS)
        try:
            summary = transcode_video_directory(
                input_dir=options_payload.input_dir,
                output_dir=options_payload.output_dir,
                filename=filename,
                recursive=options_payload.recursive,
                overwrite=options_payload.overwrite,
                dry_run=options_payload.dry_run,
                allow_unmanaged_output=options_payload.allow_unmanaged_output,
                force_cpu=options_payload.force_cpu,
                quality_mode=options_payload.quality_mode,
                extensions=extensions,
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        payload = summary.as_dict()
        if options_payload.json:
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "video transcode complete: "
                    f"scanned={summary.scanned_files} "
                    f"planned={summary.planned_files} "
                    f"transcoded={summary.transcoded_files} "
                    f"skipped={summary.skipped_files} "
                    f"failed={summary.failed_files}"
                )
            )

        if summary.failed_files:
            raise CommandError(
                f"Video transcode failed for {summary.failed_files} file(s)."
            )
        if options_payload.fail_on_skipped and summary.skipped_files:
            raise CommandError(
                f"Video transcode skipped {summary.skipped_files} selected file(s)."
            )

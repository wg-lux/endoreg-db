from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from endoreg_db.services.video_format_reconciliation import VIDEO_EXTENSIONS
from endoreg_db.services.video_transcoding import transcode_video_directory


class Command(BaseCommand):
    help = (
        "Transcode local video files from --input-dir into --output-dir using "
        "the configured system video standard."
    )

    def add_arguments(self, parser) -> None:
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

    def handle(self, *args, **options) -> None:
        try:
            summary = transcode_video_directory(
                input_dir=options["input_dir"],
                output_dir=options["output_dir"],
                filename=options["filename"],
                recursive=bool(options["recursive"]),
                overwrite=bool(options["overwrite"]),
                dry_run=bool(options["dry_run"]),
                allow_unmanaged_output=bool(options["allow_unmanaged_output"]),
                force_cpu=bool(options["force_cpu"]),
                quality_mode=str(options["quality_mode"]),
                extensions=tuple(options["extension"] or VIDEO_EXTENSIONS),
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        payload = summary.as_dict()
        if options["json"]:
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
        if options["fail_on_skipped"] and summary.skipped_files:
            raise CommandError(
                f"Video transcode skipped {summary.skipped_files} selected file(s)."
            )

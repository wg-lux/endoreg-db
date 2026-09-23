from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError, CommandParser
from lx_dtypes.models.contracts.management_command import (
    ReconcileVideoFormatsCommandOptionsPayload,
)

from endoreg_db.config.env import env_int
from endoreg_db.services.video_format_reconciliation import (
    DEFAULT_MIN_FREE_BYTES,
    VIDEO_EXTENSIONS,
    VideoFormatSummary,
    reconcile_video_formats,
)


class Command(BaseCommand):
    help = (
        "Audit managed video files for the filewatcher-standard format "
        "and optionally repair non-compliant MP4 files in place."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--root",
            action="append",
            default=[],
            help=(
                "Managed media root to scan. May be provided multiple times. "
                "When omitted, default canonical storage video roots are scanned."
            ),
        )
        parser.add_argument(
            "--include-default-roots",
            action="store_true",
            help="Scan default managed roots in addition to explicit --root values.",
        )
        parser.add_argument(
            "--no-default-roots",
            action="store_true",
            help="Do not scan default managed roots.",
        )
        parser.add_argument(
            "--include-legacy-roots",
            action="store_true",
            help=(
                "Also scan top-level legacy compatibility roots under DATA_DIR. "
                "Legacy roots are audit-only and repair is always skipped."
            ),
        )
        parser.add_argument(
            "--extension",
            action="append",
            default=[],
            help=(
                "Video extension to include, for example .mp4. May be provided "
                "multiple times. Defaults to common video extensions."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report planned repair actions without mutating files.",
        )
        parser.add_argument(
            "--repair",
            action="store_true",
            help="Attempt repair for non-compliant files.",
        )
        parser.add_argument(
            "--in-place",
            action="store_true",
            help=(
                "Allow in-place replacement of non-compliant MP4 files after "
                "successful transcode and verification."
            ),
        )
        parser.add_argument(
            "--allow-unmanaged-root",
            action="store_true",
            help=(
                "Allow scanning roots outside the configured protected/data roots. "
                "Intended for one-off operator diagnostics, not recurring services."
            ),
        )
        parser.add_argument(
            "--include-compliant",
            action="store_true",
            help="Include compliant files in the JSON report.",
        )
        parser.add_argument(
            "--max-files",
            type=int,
            default=None,
            help="Stop after checking this many video files.",
        )
        parser.add_argument(
            "--min-free-bytes",
            type=int,
            default=env_int(
                "ENDOREG_VIDEO_FORMAT_MIN_FREE_BYTES",
                DEFAULT_MIN_FREE_BYTES,
            ),
            help="Minimum free bytes required before each repair.",
        )
        parser.add_argument(
            "--force-cpu",
            action="store_true",
            help="Force CPU H.264 encoding instead of automatic NVENC selection.",
        )
        parser.add_argument(
            "--fail-on-non-compliant",
            action="store_true",
            help="Exit non-zero when non-compliant, invalid, skipped, or failed files remain.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit the summary as JSON.",
        )

    def handle(self, *args: object, **options: object) -> None:
        options_payload = ReconcileVideoFormatsCommandOptionsPayload.model_validate(
            options
        )
        self._validate_repair_mode(options_payload)
        summary = self._reconcile(options_payload)
        self._write_summary(summary, json_output=options_payload.json_output)
        self._raise_for_unresolved_issues(
            summary,
            fail_on_non_compliant=options_payload.fail_on_non_compliant,
        )

    @staticmethod
    def _validate_repair_mode(
        options: ReconcileVideoFormatsCommandOptionsPayload,
    ) -> None:
        if options.repair and not options.in_place and not options.dry_run:
            raise CommandError(
                "Repair is intentionally disabled unless --in-place or --dry-run "
                "is supplied. Re-run with --dry-run first, then --repair --in-place."
            )

    @staticmethod
    def _root_selection(
        options: ReconcileVideoFormatsCommandOptionsPayload,
    ) -> tuple[list[str], bool, bool]:
        explicit_roots = options.root
        include_default_roots = options.include_default_roots or not explicit_roots
        if options.no_default_roots:
            include_default_roots = False
        include_legacy_roots = options.include_legacy_roots
        if (
            not include_default_roots
            and not explicit_roots
            and not include_legacy_roots
        ):
            raise CommandError("No scan roots selected.")
        return explicit_roots, include_default_roots, include_legacy_roots

    @classmethod
    def _reconcile(
        cls,
        options: ReconcileVideoFormatsCommandOptionsPayload,
    ) -> VideoFormatSummary:
        explicit_roots, include_default_roots, include_legacy_roots = (
            cls._root_selection(options)
        )
        return reconcile_video_formats(
            roots=explicit_roots,
            include_default_roots=include_default_roots,
            include_legacy_roots=include_legacy_roots,
            dry_run=options.dry_run,
            repair=options.repair,
            in_place=options.in_place,
            allow_unmanaged_roots=options.allow_unmanaged_root,
            include_compliant=options.include_compliant,
            max_files=options.max_files or None,
            min_free_bytes=options.min_free_bytes,
            force_cpu=options.force_cpu,
            extensions=tuple(options.extension or VIDEO_EXTENSIONS),
        )

    def _write_summary(
        self,
        summary: VideoFormatSummary,
        *,
        json_output: bool,
    ) -> None:
        payload = summary.as_dict()
        if json_output:
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
            return
        self.stdout.write(
            self.style.SUCCESS(
                "video format reconciliation complete: "
                f"checked={summary.checked_files} "
                f"compliant={summary.compliant_files} "
                f"non_compliant={summary.non_compliant_files} "
                f"invalid={summary.invalid_files} "
                f"repaired={summary.repaired_files} "
                f"repair_failed={summary.repair_failed_files} "
                f"skipped={summary.skipped_files}"
            )
        )

    @staticmethod
    def _raise_for_unresolved_issues(
        summary: VideoFormatSummary,
        *,
        fail_on_non_compliant: bool,
    ) -> None:
        unresolved = (
            summary.non_compliant_files + summary.invalid_files - summary.repaired_files
        )
        if fail_on_non_compliant and unresolved > 0:
            raise CommandError(
                f"Video format reconciliation found {unresolved} issues."
            )

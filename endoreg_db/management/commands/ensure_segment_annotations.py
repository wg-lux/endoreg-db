from __future__ import annotations

from typing import Sequence
from typing import TypedDict, cast

from django.core.management.base import BaseCommand, CommandError, CommandParser
from lx_dtypes.models.contracts import validate_segment_annotation_ensure_payload
from lx_dtypes.models.contracts.json_types import JsonValue

from endoreg_db.management.commands._profiling import (
    add_profiling_arguments,
    command_profiling_config_from_options,
    run_with_optional_profile,
)
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.segment_annotations import ensure_segment_annotations


type _CommandOption = None | bool | int | list[int] | str
type _IdSequence = None | Sequence[int]


class _SegmentAnnotationSummary(TypedDict):
    total_segments: int
    segments_processed: int
    skipped_no_label: int
    skipped_no_frames: int
    annotations_needed: int
    annotations_created: int


class Command(BaseCommand):
    help = (
        "Populate ImageClassificationAnnotation rows for LabelVideoSegments that "
        "currently have no annotations so that export pipelines see the segment labels."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--video-id",
            dest="video_ids",
            type=int,
            action="append",
            help="Video ID to limit the migration. Can be passed multiple times.",
        )
        parser.add_argument(
            "--segment-id",
            dest="segment_ids",
            type=int,
            action="append",
            help="Segment ID to limit the migration. Can be passed multiple times.",
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
        add_profiling_arguments(parser)

    def handle(self, *args: str, **options: _CommandOption) -> None:
        profiling_config = command_profiling_config_from_options(options)
        return run_with_optional_profile(
            lambda: self._handle_unprofiled(*args, **options),
            config=profiling_config,
        )

    def _handle_unprofiled(self, *args: str, **options: _CommandOption) -> None:
        _ = args
        all_videos = self._bool_option(options, "all_videos")
        dry_run = self._bool_option(options, "dry_run")
        payload = validate_segment_annotation_ensure_payload(
            {
                "video_ids": self._id_list_option(options, "video_ids"),
                "segment_ids": self._id_list_option(options, "segment_ids"),
                "information_source_name": self._string_option(
                    options,
                    "information_source_name",
                ),
            },
            default_information_source_name="manual_annotation",
        )
        video_ids: _IdSequence = payload.video_ids
        segment_ids: _IdSequence = payload.segment_ids

        if not all_videos and not video_ids and not segment_ids:
            raise CommandError(
                "Specify --video-id (once or multiple times), --segment-id, or --all-videos."
            )

        if all_videos:
            video_ids = list(VideoFile.objects.values_list("id", flat=True))

        commit = not dry_run

        summary = cast(
            _SegmentAnnotationSummary,
            ensure_segment_annotations(
                video_ids=video_ids,
                segment_ids=segment_ids,
                information_source_name=payload.information_source_name,
                commit=commit,
            ),
        )

        if dry_run:
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

    @staticmethod
    def _bool_option(options: dict[str, _CommandOption], name: str) -> bool:
        value = options.get(name, False)
        if not isinstance(value, bool):
            raise CommandError(f"Option {name} must be a boolean flag.")
        return value

    @staticmethod
    def _id_list_option(
        options: dict[str, _CommandOption],
        name: str,
    ) -> list[JsonValue] | None:
        value = options.get(name)
        if value is None:
            return None
        if not isinstance(value, list) or any(isinstance(item, bool) for item in value):
            raise CommandError(f"Option {name} must be a list of integers.")
        return list(value)

    @staticmethod
    def _string_option(
        options: dict[str, _CommandOption],
        name: str,
    ) -> str | None:
        value = options.get(name)
        if value is not None and not isinstance(value, str):
            raise CommandError(f"Option {name} must be a string.")
        return value

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict, Unpack

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db.models import QuerySet
from lx_dtypes.models.contracts.management_command import (
    MigrateVideoStreamableStorageCommandOptionsPayload,
)

from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.hls_media import materialize_video_hls
from endoreg_db.services.streamable_media import (
    sync_video_streamable_artifacts,
)


class MigrateVideoStreamableStorageCommandOptions(TypedDict):
    video_ids: list[int] | None
    processed_only: bool
    raw_only: bool
    dry_run: bool
    regenerate: bool


MigrationStatus = Literal["failed", "migrated", "replaced", "unchanged"]


@dataclass(frozen=True)
class VideoMigrationResult:
    video_id: int
    status: MigrationStatus
    update_fields: tuple[str, ...] = ()
    selected_streamable_count: int = 0
    hls_materialized: bool = False
    error: str = ""


@dataclass
class MigrationSummary:
    migrated: int = 0
    hls_materialized: int = 0
    unchanged: int = 0
    failed: int = 0

    def record(self, result: VideoMigrationResult) -> None:
        if result.status == "migrated":
            self.migrated += 1
        elif result.status == "unchanged":
            self.unchanged += 1
        elif result.status == "failed":
            self.failed += 1
        if result.hls_materialized:
            self.hls_materialized += 1


def _selected_streamable_artifact_count(
    video: VideoFile,
    *,
    include_raw: bool,
    include_processed: bool,
) -> int:
    return sum(
        1
        for enabled, attr in (
            (include_raw, "raw_streamable_relative_path"),
            (include_processed, "processed_streamable_relative_path"),
        )
        if enabled and str(getattr(video, attr, "") or "").strip()
    )


def _processed_hls_required_for_legacy_streamable(
    video: VideoFile,
    *,
    include_processed: bool,
) -> bool:
    if not include_processed:
        return False
    return bool(
        str(getattr(video, "processed_streamable_relative_path", "") or "").strip()
    )


def _selected_videos(video_ids: list[int]) -> QuerySet[VideoFile]:
    queryset = VideoFile.objects.all().order_by("pk")
    if video_ids:
        queryset = queryset.filter(pk__in=video_ids)
    return queryset


def _migrate_streamable_video(
    video: VideoFile,
    *,
    include_raw: bool,
    include_processed: bool,
    dry_run: bool,
    regenerate: bool,
) -> VideoMigrationResult:
    selected_streamable_count = _selected_streamable_artifact_count(
        video,
        include_raw=include_raw,
        include_processed=include_processed,
    )
    processed_hls_required = _processed_hls_required_for_legacy_streamable(
        video,
        include_processed=include_processed,
    )
    if processed_hls_required and not getattr(video.processed_file, "name", ""):
        raise RuntimeError(
            "Cannot replace processed streamable artifact without "
            "canonical processed_file"
        )
    update_fields = sync_video_streamable_artifacts(
        video,
        include_raw=include_raw,
        include_processed=include_processed,
        save=not dry_run,
        force=False,
    )
    if processed_hls_required and not dry_run:
        materialize_video_hls(
            int(video.pk),
            artifact_kind="processed",
            force=regenerate,
        )
    status: MigrationStatus = "unchanged"
    if update_fields:
        status = "migrated"
    elif selected_streamable_count:
        status = "replaced"
    return VideoMigrationResult(
        video_id=int(video.pk),
        status=status,
        update_fields=tuple(update_fields),
        selected_streamable_count=selected_streamable_count,
        hls_materialized=processed_hls_required,
    )


class Command(BaseCommand):
    help = (
        "Replace legacy plaintext streamable MP4 artifacts with encrypted HLS "
        "where possible, then securely delete the legacy MP4 paths."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--video-id",
            type=int,
            action="append",
            dest="video_ids",
            help="Restrict migration to one or more specific VideoFile IDs.",
        )
        parser.add_argument(
            "--processed-only",
            action="store_true",
            help="Only synchronize processed video artifacts.",
        )
        parser.add_argument(
            "--raw-only",
            action="store_true",
            help="Only synchronize raw video artifacts.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without copying files or saving metadata.",
        )
        parser.add_argument(
            "--regenerate",
            "--force",
            action="store_true",
            dest="regenerate",
            help=(
                "Regenerate selected processed HLS artifacts even when an "
                "existing HLS artifact is ready."
            ),
        )

    def handle(
        self,
        *args: str,
        **options: Unpack[MigrateVideoStreamableStorageCommandOptions],
    ) -> None:
        _ = args
        options_payload = (
            MigrateVideoStreamableStorageCommandOptionsPayload.model_validate(options)
        )
        self._validate_options(options_payload)
        regenerate = bool(options.get("regenerate", False))
        include_raw = not options_payload.processed_only
        include_processed = not options_payload.raw_only
        summary = MigrationSummary()
        for video in _selected_videos(options_payload.video_ids).iterator():
            result = self._migrate_video_safely(
                video,
                include_raw=include_raw,
                include_processed=include_processed,
                dry_run=options_payload.dry_run,
                regenerate=regenerate,
            )
            summary.record(result)
            self._write_result(result, dry_run=options_payload.dry_run)
        self._write_summary(summary)

    @staticmethod
    def _validate_options(
        options: MigrateVideoStreamableStorageCommandOptionsPayload,
    ) -> None:
        if options.processed_only and options.raw_only:
            raise CommandError(
                "--processed-only and --raw-only cannot be used together"
            )

    @staticmethod
    def _migrate_video_safely(
        video: VideoFile,
        *,
        include_raw: bool,
        include_processed: bool,
        dry_run: bool,
        regenerate: bool,
    ) -> VideoMigrationResult:
        try:
            return _migrate_streamable_video(
                video,
                include_raw=include_raw,
                include_processed=include_processed,
                dry_run=dry_run,
                regenerate=regenerate,
            )
        except Exception as exc:
            return VideoMigrationResult(
                video_id=int(video.pk),
                status="failed",
                error=str(exc),
            )

    def _write_result(self, result: VideoMigrationResult, *, dry_run: bool) -> None:
        if result.status == "failed":
            self.stderr.write(
                self.style.ERROR(
                    f"video={result.video_id} failed to synchronize "
                    f"streamable media: {result.error}"
                )
            )
            return
        if result.status == "migrated":
            action = "would update" if dry_run else "updated"
            self.stdout.write(
                self.style.SUCCESS(
                    f"video={result.video_id} {action}: "
                    f"{', '.join(result.update_fields)}"
                )
            )
            return
        if result.status == "replaced":
            action = "would replace" if dry_run else "replaced"
            self.stdout.write(
                self.style.SUCCESS(
                    f"video={result.video_id} {action}: "
                    f"{result.selected_streamable_count} streamable artifact(s)"
                )
            )
            return
        self.stdout.write(f"video={result.video_id} unchanged")

    def _write_summary(self, counts: MigrationSummary) -> None:
        summary = (
            f"streamable video migration complete: migrated={counts.migrated} "
            f"hls_materialized={counts.hls_materialized} "
            f"unchanged={counts.unchanged} failed={counts.failed}"
        )
        if counts.failed:
            self.stderr.write(self.style.WARNING(summary))
        else:
            self.stdout.write(self.style.SUCCESS(summary))

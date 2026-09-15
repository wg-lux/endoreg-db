# pyright: reportPrivateUsage=false
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from typing import Any, cast
from uuid import uuid4

from django.core.management.base import CommandError, CommandParser

from endoreg_db.config.env import video_storage_destructive_migration_enabled
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.video_files._io import _delete_raw_file_after_validation
from endoreg_db.services.video_processed_transcode import (
    ProcessedVideoTranscodeResult,
    transcode_processed_video_for_storage_pressure,
)
from endoreg_db.services.video_storage_normalization import (
    VideoStorageCapacityReport,
    VideoStorageInventoryReport,
    VideoStorageProfile,
    configured_video_storage_profile,
    inventory_video_storage,
    raw_cleanup_blockers,
    video_storage_capacity,
)
from endoreg_db.utils import paths as path_utils
from endoreg_db.utils.structured_logging import emit_structured_event

from ._video_command_base import BaseVideoCommand

logger = logging.getLogger(__name__)

DEFAULT_INVENTORY_BATCH_SIZE = 100
MAX_INVENTORY_BATCH_SIZE = 1000


@dataclass(frozen=True)
class _RunOptions:
    apply_changes: bool
    cleanup_validated_raw: bool
    video_ids: list[int] | None
    limit: int
    after_video_id: int
    quality_mode: str
    force_cpu: bool
    json_output: bool


_InventoryRow = tuple[VideoFile, VideoStorageInventoryReport]


class Command(BaseVideoCommand):
    help = (
        "Inventory video storage and, only when the production gate is enabled, "
        "normalize processed masters before removing validated raw derivatives."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        self.add_video_selection_arguments(
            parser,
            limit_help=(
                "Maximum videos after sorting by reclaimable bytes descending."
            ),
        )
        parser.set_defaults(limit=DEFAULT_INVENTORY_BATCH_SIZE)
        parser.add_argument(
            "--after-video-id",
            type=int,
            default=0,
            help=(
                "Resume inventory after this VideoFile primary key. Selection "
                "is bounded before filesystem inspection."
            ),
        )
        self.add_apply_argument(
            parser,
            help_text=(
                "Transcode selected processed videos. Disabled unless "
                "ENDOREG_VIDEO_STORAGE_DESTRUCTIVE_MIGRATION_ENABLED=true."
            ),
        )
        self.add_json_output_argument(parser)
        parser.add_argument(
            "--quality-mode",
            choices=("fast", "balanced", "quality"),
            default="balanced",
        )
        parser.add_argument("--force-cpu", action="store_true")
        parser.add_argument(
            "--cleanup-validated-raw",
            action="store_true",
            help=(
                "After verified normalization, remove canonical raw, raw "
                "streamable, and raw HLS artifacts for validated videos."
            ),
        )

    def handle(self, *args: object, **options: object) -> None:
        run_options = self._run_options(options)
        self._validate_mutation_options(run_options)
        inventory_rows = self._inventory_rows(run_options)
        batch_id = uuid4().hex
        self._validate_reconciliation(run_options, inventory_rows)
        capacity = self._capacity(inventory_rows)
        self._validate_capacity(run_options, capacity)
        self._emit_batch_started(batch_id, run_options, inventory_rows, capacity)
        results, reconciliation_failed = self._process_rows(
            batch_id=batch_id,
            run_options=run_options,
            inventory_rows=inventory_rows,
        )
        payload = self._summary_payload(
            apply_changes=run_options.apply_changes,
            cleanup_validated_raw=run_options.cleanup_validated_raw,
            rows=[item[1] for item in inventory_rows],
            results=results,
            batch_id=batch_id,
            capacity=capacity.as_dict(),
            after_video_id=run_options.after_video_id,
            batch_limit=run_options.limit,
            next_after_video_id=self._next_inventory_cursor(
                inventory_rows,
                reconciliation_failed=reconciliation_failed,
            ),
        )
        self._write_summary(payload, run_options.json_output)
        self._raise_on_failed_results(payload, reconciliation_failed)

    def _run_options(self, options: dict[str, object]) -> _RunOptions:
        limit = self.positive_limit_from_options(options)
        if limit is None:
            limit = DEFAULT_INVENTORY_BATCH_SIZE
        if limit > MAX_INVENTORY_BATCH_SIZE:
            raise CommandError(f"--limit must not exceed {MAX_INVENTORY_BATCH_SIZE}")
        raw_after_video_id = options.get("after_video_id", 0)
        if not isinstance(raw_after_video_id, int) or raw_after_video_id < 0:
            raise CommandError("--after-video-id must be a non-negative integer")
        return _RunOptions(
            apply_changes=bool(options.get("apply")),
            cleanup_validated_raw=bool(options.get("cleanup_validated_raw")),
            video_ids=self.selected_video_ids_from_options(options),
            limit=limit,
            after_video_id=raw_after_video_id,
            quality_mode=str(options.get("quality_mode") or "balanced"),
            force_cpu=bool(options.get("force_cpu")),
            json_output=bool(options.get("json_output")),
        )

    @staticmethod
    def _validate_mutation_options(run_options: _RunOptions) -> None:
        if run_options.cleanup_validated_raw and not run_options.apply_changes:
            raise CommandError("--cleanup-validated-raw requires --apply")
        if (
            run_options.apply_changes
            and not video_storage_destructive_migration_enabled()
        ):
            raise CommandError(
                "Destructive video migration is disabled. Verify temporal/frame "
                "quality gates, then set "
                "ENDOREG_VIDEO_STORAGE_DESTRUCTIVE_MIGRATION_ENABLED=true."
            )

    @staticmethod
    def _inventory_rows(run_options: _RunOptions) -> list[_InventoryRow]:
        queryset = (
            VideoFile.objects.select_related("state")
            .prefetch_related("hls_artifacts")
            .filter(pk__gt=run_options.after_video_id)
            .order_by("pk")
        )
        if run_options.video_ids:
            queryset = queryset.filter(pk__in=run_options.video_ids)
        bounded_videos = list(queryset[: run_options.limit])
        inventory_rows = [
            (video, inventory_video_storage(video)) for video in bounded_videos
        ]
        inventory_rows.sort(
            key=lambda item: (
                item[1].reclaimable_raw_bytes,
                item[1].total_bytes,
                item[0].pk,
            ),
            reverse=True,
        )
        return inventory_rows

    @staticmethod
    def _validate_reconciliation(
        run_options: _RunOptions,
        inventory_rows: list[_InventoryRow],
    ) -> None:
        unreconciled_video_ids = [
            report.video_id for _, report in inventory_rows if not report.reconciled
        ]
        if run_options.apply_changes and unreconciled_video_ids:
            raise CommandError(
                "Database/filesystem reconciliation failed before mutation for "
                f"video IDs {unreconciled_video_ids}."
            )

    @staticmethod
    def _capacity(
        inventory_rows: list[_InventoryRow],
    ) -> VideoStorageCapacityReport:
        projected_temporary_bytes = max(
            (math.ceil(report.processed_bytes * 1.1) for _, report in inventory_rows),
            default=0,
        )
        return video_storage_capacity(
            storage_root=path_utils.protected_media_root(),
            projected_temporary_bytes=projected_temporary_bytes,
        )

    @staticmethod
    def _next_inventory_cursor(
        inventory_rows: list[_InventoryRow],
        *,
        reconciliation_failed: bool,
    ) -> int | None:
        if reconciliation_failed:
            return None
        return max(
            (int(video.pk) for video, _ in inventory_rows),
            default=None,
        )

    @staticmethod
    def _validate_capacity(
        run_options: _RunOptions,
        capacity: VideoStorageCapacityReport,
    ) -> None:
        if run_options.apply_changes and capacity.status == "stop":
            raise CommandError(
                "Storage hard-stop threshold reached after projected temporary "
                "output; no files were changed."
            )

    @staticmethod
    def _emit_batch_started(
        batch_id: str,
        run_options: _RunOptions,
        inventory_rows: list[_InventoryRow],
        capacity: VideoStorageCapacityReport,
    ) -> None:
        emit_structured_event(
            logger,
            "video_storage_normalization.batch_started",
            batch_id=batch_id,
            selected=len(inventory_rows),
            apply=run_options.apply_changes,
            capacity_status=capacity.status,
            capacity_free_bytes=capacity.free_bytes,
            capacity_projected_temporary_bytes=capacity.projected_temporary_bytes,
            capacity_warning_free_bytes=capacity.warning_free_bytes,
            capacity_stop_free_bytes=capacity.stop_free_bytes,
        )

    @classmethod
    def _process_rows(
        cls,
        *,
        batch_id: str,
        run_options: _RunOptions,
        inventory_rows: list[_InventoryRow],
    ) -> tuple[list[dict[str, Any]], bool]:
        results: list[dict[str, Any]] = []
        for video, before in inventory_rows:
            result_payload, reconciled = cls._process_row(
                video=video,
                before=before,
                run_options=run_options,
            )
            results.append(result_payload)
            emit_structured_event(
                logger,
                "video_storage_normalization.video_completed",
                batch_id=batch_id,
                video_id=int(video.pk),
                result=result_payload,
            )
            if run_options.apply_changes and not reconciled:
                return results, True
        return results, False

    @classmethod
    def _process_row(
        cls,
        *,
        video: VideoFile,
        before: VideoStorageInventoryReport,
        run_options: _RunOptions,
    ) -> tuple[dict[str, Any], bool]:
        transcode_result: ProcessedVideoTranscodeResult | None = None
        raw_cleanup_performed = False
        if run_options.apply_changes:
            transcode_result = transcode_processed_video_for_storage_pressure(
                video,
                apply=True,
                quality_mode=run_options.quality_mode,
                force_cpu=run_options.force_cpu,
            )
            video.refresh_from_db()
            raw_cleanup_performed = cls._cleanup_raw_if_allowed(video, run_options)
        after = inventory_video_storage(video)
        return {
            "before": before.as_dict(),
            "after": after.as_dict(),
            "bytes_reclaimed": max(0, before.total_bytes - after.total_bytes),
            "raw_cleanup_performed": raw_cleanup_performed,
            "transcode": (
                cls._transcode_payload(transcode_result)
                if transcode_result is not None
                else None
            ),
            "reconciled": after.reconciled,
        }, after.reconciled

    @staticmethod
    def _cleanup_raw_if_allowed(
        video: VideoFile,
        run_options: _RunOptions,
    ) -> bool:
        after_transcode = inventory_video_storage(video)
        cleanup_allowed = (
            run_options.cleanup_validated_raw
            and after_transcode.reconciled
            and after_transcode.anonymization_validated
            and after_transcode.normalization_verified
            and not raw_cleanup_blockers(video)
        )
        if not cleanup_allowed:
            return False
        raw_cleanup_performed = _delete_raw_file_after_validation(video)
        video.refresh_from_db()
        return raw_cleanup_performed

    def _write_summary(self, payload: dict[str, Any], json_output: bool) -> None:
        if json_output:
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
            return
        self.stdout.write(
            "video storage normalization: "
            f"selected={payload['selected']} "
            f"occupied_bytes={payload['occupied_bytes']} "
            f"reclaimable_raw_bytes={payload['reclaimable_raw_bytes']} "
            f"reclaimed_bytes={payload['reclaimed_bytes']} "
            f"apply={payload['apply']}"
        )

    @staticmethod
    def _raise_on_failed_results(
        payload: dict[str, Any],
        reconciliation_failed: bool,
    ) -> None:
        failed = int(payload["failed_videos"])
        if failed:
            raise CommandError(f"Normalization failed for {failed} video(s).")
        if reconciliation_failed:
            raise CommandError(
                "Database/filesystem reconciliation failed after a batch item; "
                "remaining destructive work was stopped."
            )

    @staticmethod
    def _transcode_payload(
        result: ProcessedVideoTranscodeResult,
    ) -> dict[str, int | str]:
        return {
            "status": result.status,
            "old_size": result.old_size,
            "new_size": result.new_size,
            "detail": result.detail,
        }

    @staticmethod
    def _summary_payload(
        *,
        apply_changes: bool,
        cleanup_validated_raw: bool,
        rows: list[VideoStorageInventoryReport],
        results: list[dict[str, Any]],
        batch_id: str,
        capacity: dict[str, int | str],
        after_video_id: int,
        batch_limit: int,
        next_after_video_id: int | None,
    ) -> dict[str, Any]:
        profile = configured_video_storage_profile()
        inventory_counts = Command._inventory_counts(rows)
        result_counts = Command._result_counts(results)
        return {
            "apply": apply_changes,
            "batch_id": batch_id,
            "inventory_cursor": {
                "after_video_id": after_video_id,
                "next_after_video_id": next_after_video_id,
                "batch_limit": batch_limit,
            },
            "cleanup_validated_raw": cleanup_validated_raw,
            "capacity": capacity,
            "profile": Command._profile_payload(profile),
            "selected": len(rows),
            **inventory_counts,
            **result_counts,
            "results": results,
        }

    @staticmethod
    def _profile_payload(profile: VideoStorageProfile) -> dict[str, object]:
        return {
            "name": profile.name,
            "max_bit_rate_bps": profile.max_bit_rate_bps,
            "max_bytes_per_second": profile.max_bytes_per_second,
            "fixed_overhead_bytes": profile.fixed_overhead_bytes,
            "max_width": profile.max_width,
            "max_height": profile.max_height,
            "max_source_fps": profile.max_source_fps,
            "annotation_max_fps": profile.annotation_max_fps,
        }

    @staticmethod
    def _inventory_counts(rows: list[VideoStorageInventoryReport]) -> dict[str, int]:
        counts = {
            "normalized_videos": 0,
            "pending_videos": 0,
            "reconciliation_error_videos": 0,
            "incomplete_hls_inventory_artifacts": 0,
            "occupied_bytes": 0,
            "raw_bytes": 0,
            "processed_bytes": 0,
            "raw_hls_bytes": 0,
            "processed_hls_bytes": 0,
            "reclaimable_raw_bytes": 0,
        }
        for row in rows:
            counts["normalized_videos"] += int(row.normalization_verified)
            counts["pending_videos"] += int(not row.normalization_verified)
            counts["reconciliation_error_videos"] += int(not row.reconciled)
            counts["incomplete_hls_inventory_artifacts"] += (
                row.incomplete_hls_inventory_artifacts
            )
            counts["occupied_bytes"] += row.total_bytes
            counts["raw_bytes"] += row.raw_bytes
            counts["processed_bytes"] += row.processed_bytes
            counts["raw_hls_bytes"] += row.raw_hls_bytes
            counts["processed_hls_bytes"] += row.processed_hls_bytes
            counts["reclaimable_raw_bytes"] += row.reclaimable_raw_bytes
        return counts

    @staticmethod
    def _result_counts(results: list[dict[str, Any]]) -> dict[str, int]:
        reclaimed_bytes = 0
        failed_videos = 0
        for result in results:
            reclaimed_bytes += int(result["bytes_reclaimed"])
            raw_transcode = result.get("transcode")
            if not isinstance(raw_transcode, dict):
                continue
            transcode = cast(dict[str, object], raw_transcode)
            failed_videos += int(transcode.get("status") == "failed")
        return {
            "reclaimed_bytes": reclaimed_bytes,
            "failed_videos": failed_videos,
        }

# pyright: reportPrivateUsage=false
from __future__ import annotations

import json
import logging
import math
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
    VideoStorageInventoryReport,
    configured_video_storage_profile,
    inventory_video_storage,
    raw_cleanup_blockers,
    video_storage_capacity,
)
from endoreg_db.utils import paths as path_utils
from endoreg_db.utils.structured_logging import emit_structured_event

from ._video_command_base import BaseVideoCommand

logger = logging.getLogger(__name__)


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
        apply_changes = bool(options.get("apply"))
        cleanup_validated_raw = bool(options.get("cleanup_validated_raw"))
        if cleanup_validated_raw and not apply_changes:
            raise CommandError("--cleanup-validated-raw requires --apply")
        if apply_changes and not video_storage_destructive_migration_enabled():
            raise CommandError(
                "Destructive video migration is disabled. Verify temporal/frame "
                "quality gates, then set "
                "ENDOREG_VIDEO_STORAGE_DESTRUCTIVE_MIGRATION_ENABLED=true."
            )

        video_ids = self.selected_video_ids_from_options(options)
        limit = self.positive_limit_from_options(options)
        queryset = VideoFile.objects.select_related("state").prefetch_related(
            "hls_artifacts"
        )
        if video_ids:
            queryset = queryset.filter(pk__in=video_ids)

        inventory_rows = [(video, inventory_video_storage(video)) for video in queryset]
        inventory_rows.sort(
            key=lambda item: (
                item[1].reclaimable_raw_bytes,
                item[1].total_bytes,
                item[0].pk,
            ),
            reverse=True,
        )
        if limit is not None:
            inventory_rows = inventory_rows[:limit]

        batch_id = uuid4().hex
        unreconciled_video_ids = [
            report.video_id for _, report in inventory_rows if not report.reconciled
        ]
        if apply_changes and unreconciled_video_ids:
            raise CommandError(
                "Database/filesystem reconciliation failed before mutation for "
                f"video IDs {unreconciled_video_ids}."
            )
        projected_temporary_bytes = max(
            (math.ceil(report.processed_bytes * 1.1) for _, report in inventory_rows),
            default=0,
        )
        capacity = video_storage_capacity(
            storage_root=path_utils.protected_media_root(),
            projected_temporary_bytes=projected_temporary_bytes,
        )
        if apply_changes and capacity.status == "stop":
            raise CommandError(
                "Storage hard-stop threshold reached after projected temporary "
                "output; no files were changed."
            )
        emit_structured_event(
            logger,
            "video_storage_normalization.batch_started",
            batch_id=batch_id,
            selected=len(inventory_rows),
            apply=apply_changes,
            capacity_status=capacity.status,
            capacity_free_bytes=capacity.free_bytes,
            capacity_projected_temporary_bytes=capacity.projected_temporary_bytes,
            capacity_warning_free_bytes=capacity.warning_free_bytes,
            capacity_stop_free_bytes=capacity.stop_free_bytes,
        )

        quality_mode = str(options.get("quality_mode") or "balanced")
        force_cpu = bool(options.get("force_cpu"))
        results: list[dict[str, Any]] = []
        reconciliation_failed = False
        for video, before in inventory_rows:
            transcode_result: ProcessedVideoTranscodeResult | None = None
            raw_cleanup_performed = False
            if apply_changes:
                transcode_result = transcode_processed_video_for_storage_pressure(
                    video,
                    apply=True,
                    quality_mode=quality_mode,
                    force_cpu=force_cpu,
                )
                video.refresh_from_db()
                after_transcode = inventory_video_storage(video)
                if (
                    cleanup_validated_raw
                    and after_transcode.anonymization_validated
                    and after_transcode.normalization_verified
                    and not raw_cleanup_blockers(video)
                ):
                    raw_cleanup_performed = _delete_raw_file_after_validation(video)
                    video.refresh_from_db()

            after = inventory_video_storage(video)
            result_payload: dict[str, Any] = {
                "before": before.as_dict(),
                "after": after.as_dict(),
                "bytes_reclaimed": max(0, before.total_bytes - after.total_bytes),
                "raw_cleanup_performed": raw_cleanup_performed,
                "transcode": (
                    self._transcode_payload(transcode_result)
                    if transcode_result is not None
                    else None
                ),
                "reconciled": after.reconciled,
            }
            results.append(result_payload)
            emit_structured_event(
                logger,
                "video_storage_normalization.video_completed",
                batch_id=batch_id,
                video_id=int(video.pk),
                result=result_payload,
            )
            if apply_changes and not after.reconciled:
                reconciliation_failed = True
                break

        payload = self._summary_payload(
            apply_changes=apply_changes,
            cleanup_validated_raw=cleanup_validated_raw,
            rows=[item[1] for item in inventory_rows],
            results=results,
            batch_id=batch_id,
            capacity=capacity.as_dict(),
        )
        if bool(options.get("json_output")):
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
        else:
            self.stdout.write(
                "video storage normalization: "
                f"selected={payload['selected']} "
                f"occupied_bytes={payload['occupied_bytes']} "
                f"reclaimable_raw_bytes={payload['reclaimable_raw_bytes']} "
                f"reclaimed_bytes={payload['reclaimed_bytes']} "
                f"apply={apply_changes}"
            )

        failed = 0
        for result in results:
            raw_transcode = result["transcode"]
            if not isinstance(raw_transcode, dict):
                continue
            transcode_payload = cast(dict[str, object], raw_transcode)
            if transcode_payload.get("status") == "failed":
                failed += 1
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
    ) -> dict[str, Any]:
        profile = configured_video_storage_profile()
        return {
            "apply": apply_changes,
            "batch_id": batch_id,
            "cleanup_validated_raw": cleanup_validated_raw,
            "capacity": capacity,
            "profile": {
                "name": profile.name,
                "max_bit_rate_bps": profile.max_bit_rate_bps,
                "max_bytes_per_second": profile.max_bytes_per_second,
                "fixed_overhead_bytes": profile.fixed_overhead_bytes,
                "max_width": profile.max_width,
                "max_height": profile.max_height,
                "max_source_fps": profile.max_source_fps,
                "annotation_max_fps": profile.annotation_max_fps,
            },
            "selected": len(rows),
            "normalized_videos": sum(row.normalization_verified for row in rows),
            "pending_videos": sum(not row.normalization_verified for row in rows),
            "reconciliation_error_videos": sum(not row.reconciled for row in rows),
            "occupied_bytes": sum(row.total_bytes for row in rows),
            "raw_bytes": sum(row.raw_bytes for row in rows),
            "processed_bytes": sum(row.processed_bytes for row in rows),
            "raw_hls_bytes": sum(row.raw_hls_bytes for row in rows),
            "processed_hls_bytes": sum(row.processed_hls_bytes for row in rows),
            "reclaimable_raw_bytes": sum(row.reclaimable_raw_bytes for row in rows),
            "reclaimed_bytes": sum(
                int(result["bytes_reclaimed"]) for result in results
            ),
            "failed_videos": sum(
                isinstance(result.get("transcode"), dict)
                and cast(dict[str, object], result["transcode"]).get("status")
                == "failed"
                for result in results
            ),
            "results": results,
        }

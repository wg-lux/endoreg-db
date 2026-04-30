from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ContextManager, Iterable

from lx_anonymizer.anonymization.masking import MaskApplication
from lx_anonymizer.video_processing import video_utils

from endoreg_db.models import VideoFile
from endoreg_db.utils.file_operations import (
    atomic_move_file,
    safe_unlink_file,
    sha256_file,
)
from endoreg_db.utils.storage import materialize_video_file, save_local_file

logger = logging.getLogger(__name__)
PRESERVE_DIMENSIONS_MODE = "preserve_dimensions"


@dataclass(frozen=True)
class VideoDimensionBackfillResult:
    video_id: int | None
    status: str
    source_dimensions: tuple[int, int] = (0, 0)
    processed_dimensions: tuple[int, int] = (0, 0)
    repaired: bool = False
    detail: str = ""


def _coerce_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _probe_dimensions(path: Path) -> tuple[int, int]:
    info = video_utils.detect_video_format(path)
    return (_coerce_int(info.get("width")), _coerce_int(info.get("height")))


def _local_raw_context(video: VideoFile) -> ContextManager[Path]:
    return materialize_video_file(video, "raw")


def _local_processed_context(video: VideoFile) -> ContextManager[Path]:
    return materialize_video_file(video, "processed")


def _store_repaired_processed_video(
    video: VideoFile,
    temp_output: Path,
    fallback_destination: Path,
) -> None:
    processed_file = getattr(video, "processed_file", None)
    if processed_file is not None and getattr(processed_file, "name", None):
        save_local_file(
            processed_file,
            temp_output,
            name=str(processed_file.name),
            save=False,
            overwrite=True,
        )
        return

    atomic_move_file(source=temp_output, destination=fallback_destination)


def _mask_config_for_video(
    video: VideoFile,
    mask_application: MaskApplication,
) -> dict[str, Any]:
    processor = getattr(video, "processor", None)
    if processor is None:
        return dict(mask_application.default_mask_config)

    get_roi = getattr(processor, "get_roi_endoscope_image", None)
    if get_roi is None:
        return dict(mask_application.default_mask_config)

    roi = get_roi()
    if not isinstance(roi, dict):
        return dict(mask_application.default_mask_config)

    return mask_application.create_mask_config_from_roi(roi)


def backfill_video_anonymized_dimensions(
    video: VideoFile,
    *,
    dry_run: bool = False,
    mask_application: MaskApplication | None = None,
) -> VideoDimensionBackfillResult:
    """
    Regenerate a cropped anonymized video from its raw source while preserving
    source dimensions.
    """
    video_id = getattr(video, "pk", None)
    try:
        source_context = _local_raw_context(video)
    except (FileNotFoundError, ValueError) as exc:
        return VideoDimensionBackfillResult(
            video_id=video_id,
            status="missing_source",
            detail=str(exc),
        )
    try:
        processed_context = _local_processed_context(video)
    except (FileNotFoundError, ValueError) as exc:
        return VideoDimensionBackfillResult(
            video_id=video_id,
            status="missing_processed",
            detail=str(exc),
        )

    with source_context as source_path, processed_context as processed_path:
        source_path = Path(source_path)
        processed_path = Path(processed_path)
        if not source_path.is_file():
            return VideoDimensionBackfillResult(
                video_id=video_id,
                status="missing_source",
                detail=str(source_path),
            )
        if not processed_path.is_file():
            return VideoDimensionBackfillResult(
                video_id=video_id,
                status="missing_processed",
                detail=str(processed_path),
            )

        source_dimensions = _probe_dimensions(source_path)
        processed_dimensions = _probe_dimensions(processed_path)
        if source_dimensions[0] <= 0 or source_dimensions[1] <= 0:
            return VideoDimensionBackfillResult(
                video_id=video_id,
                status="source_unprobeable",
                source_dimensions=source_dimensions,
                processed_dimensions=processed_dimensions,
                detail=str(source_path),
            )
        if processed_dimensions[0] <= 0 or processed_dimensions[1] <= 0:
            return VideoDimensionBackfillResult(
                video_id=video_id,
                status="processed_unprobeable",
                source_dimensions=source_dimensions,
                processed_dimensions=processed_dimensions,
                detail=str(processed_path),
            )
        if source_dimensions == processed_dimensions:
            return VideoDimensionBackfillResult(
                video_id=video_id,
                status="already_valid",
                source_dimensions=source_dimensions,
                processed_dimensions=processed_dimensions,
            )
        if dry_run:
            return VideoDimensionBackfillResult(
                video_id=video_id,
                status="would_repair",
                source_dimensions=source_dimensions,
                processed_dimensions=processed_dimensions,
            )

        mask_application = mask_application or MaskApplication(preferred_encoder={})
        mask_config = _mask_config_for_video(video, mask_application)
        temp_output = processed_path.with_name(
            f"{processed_path.stem}.dimension-backfill.{os.getpid()}{processed_path.suffix}"
        )
        safe_unlink_file(temp_output, missing_ok=True)

        try:
            try:
                ok = mask_application.mask_video_streaming(
                    input_video=source_path,
                    mask_config=mask_config,
                    output_video=temp_output,
                    mode=PRESERVE_DIMENSIONS_MODE,
                )
            except TypeError as exc:
                if "mode" not in str(exc):
                    raise
                return VideoDimensionBackfillResult(
                    video_id=video_id,
                    status="unsupported_lx_anonymizer",
                    source_dimensions=source_dimensions,
                    processed_dimensions=processed_dimensions,
                    detail="installed lx_anonymizer does not support mask mode",
                )
            if not ok:
                return VideoDimensionBackfillResult(
                    video_id=video_id,
                    status="repair_failed",
                    source_dimensions=source_dimensions,
                    processed_dimensions=processed_dimensions,
                    detail="mask_video_streaming returned false",
                )

            repaired_dimensions = _probe_dimensions(temp_output)
            if repaired_dimensions != source_dimensions:
                return VideoDimensionBackfillResult(
                    video_id=video_id,
                    status="repair_dimension_mismatch",
                    source_dimensions=source_dimensions,
                    processed_dimensions=processed_dimensions,
                    detail=f"repaired_dimensions={repaired_dimensions}",
                )

            video.processed_video_hash = sha256_file(temp_output)
            _store_repaired_processed_video(video, temp_output, processed_path)
            video.save(update_fields=["processed_video_hash", "date_modified"])
            logger.info(
                "Repaired anonymized video dimensions for video=%s from %s to %s",
                video_id,
                processed_dimensions,
                source_dimensions,
            )
            return VideoDimensionBackfillResult(
                video_id=video_id,
                status="repaired",
                source_dimensions=source_dimensions,
                processed_dimensions=processed_dimensions,
                repaired=True,
            )
        finally:
            safe_unlink_file(temp_output, missing_ok=True)


def backfill_anonymized_video_dimensions(
    *,
    dry_run: bool = False,
    limit: int | None = None,
    videos: Iterable[VideoFile] | None = None,
) -> list[VideoDimensionBackfillResult]:
    queryset = videos
    if queryset is None:
        queryset = (
            VideoFile.objects.filter(
                raw_file__isnull=False,
                processed_file__isnull=False,
            )
            .exclude(raw_file="")
            .exclude(processed_file="")
            .order_by("pk")
        )
        if limit is not None:
            queryset = queryset[:limit]

    mask_application = MaskApplication(preferred_encoder={})
    return [
        backfill_video_anonymized_dimensions(
            video,
            dry_run=dry_run,
            mask_application=mask_application,
        )
        for video in queryset
    ]

"""Typed configuration boundary for outside-frame video blackening."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, cast

from pydantic import ValidationError

from endoreg_db.config.env import get_celery_ffmpeg_media_queue
from lx_dtypes.models.contracts.json_types import JsonValue
from lx_dtypes.models.contracts.video_segment_validation import (
    OutsideFrameBlackeningHistoryConfigData,
    OutsideFrameBlackeningHistoryConfigPayload,
    VideoSegmentValidationNull,
)

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_processing import VideoProcessingHistory

logger = logging.getLogger(__name__)

OUTSIDE_FRAME_BLACKENING_KIND = "outside_frame_blackening"
OutsideFrameBlackeningKind = Literal["outside_frame_blackening"]
LEGACY_BLACKENING_QUEUE = "inline_or_thread"


class _VideoProcessingHistoryRecord(Protocol):
    pk: int
    config: JsonValue

    def save(self, *, update_fields: list[str]) -> None: ...


class OutsideFrameBlackeningConfigError(ValueError):
    """Raised when persisted outside-frame blackening config is malformed."""


@dataclass(frozen=True)
class OutsideFrameBlackeningConfig:
    only_validated: bool
    queue: str
    kind: OutsideFrameBlackeningKind = OUTSIDE_FRAME_BLACKENING_KIND

    def to_dict(self) -> OutsideFrameBlackeningHistoryConfigData:
        return OutsideFrameBlackeningHistoryConfigPayload(
            kind=self.kind,
            only_validated=self.only_validated,
            queue=self.queue,
        ).to_config_data()


def _validate_blackening_queue(queue: JsonValue) -> str:
    if not isinstance(queue, str):
        raise OutsideFrameBlackeningConfigError("Blackening queue must be a string.")
    normalized = queue.strip()
    if not normalized:
        raise OutsideFrameBlackeningConfigError("Blackening queue must not be empty.")
    return normalized


def parse_blackening_history_config(
    config: JsonValue,
) -> OutsideFrameBlackeningConfig | VideoSegmentValidationNull:
    if not isinstance(config, dict):
        return None
    if config.get("kind") != OUTSIDE_FRAME_BLACKENING_KIND:
        return None
    try:
        payload = OutsideFrameBlackeningHistoryConfigPayload.model_validate(config)
    except ValidationError as exc:
        raise OutsideFrameBlackeningConfigError(
            f"Config for blackening did not pass the validation. {exc}"
        ) from exc
    return OutsideFrameBlackeningConfig(
        only_validated=payload.only_validated,
        queue=payload.queue,
        kind=payload.kind,
    )


def _repair_legacy_blackening_history_config(
    history_record: _VideoProcessingHistoryRecord,
) -> OutsideFrameBlackeningConfig | VideoSegmentValidationNull:
    config = history_record.config
    if not isinstance(config, dict):
        return None
    if config.get("kind") != OUTSIDE_FRAME_BLACKENING_KIND:
        return None

    canonical_keys = {"kind", "only_validated", "queue"}
    if not set(config).issubset(canonical_keys):
        return None
    if "only_validated" in config and "queue" in config:
        return None

    only_validated = config.get("only_validated", False)
    if not isinstance(only_validated, bool):
        return None
    queue = config["queue"] if "queue" in config else get_celery_ffmpeg_media_queue()
    try:
        repaired = OutsideFrameBlackeningConfig(
            only_validated=only_validated,
            queue=_validate_blackening_queue(queue),
        )
        repaired_config = cast(JsonValue, repaired.to_dict())
    except (OutsideFrameBlackeningConfigError, ValidationError):
        return None

    history_record.config = repaired_config
    history_record.save(update_fields=["config"])
    logger.warning(
        "Repaired legacy outside-frame blackening config on VideoProcessingHistory %s.",
        history_record.pk,
    )
    return repaired


def _parse_blackening_history_record(
    history_record: _VideoProcessingHistoryRecord,
) -> OutsideFrameBlackeningConfig | VideoSegmentValidationNull:
    try:
        return parse_blackening_history_config(history_record.config)
    except OutsideFrameBlackeningConfigError:
        repaired = _repair_legacy_blackening_history_config(history_record)
        if repaired is not None:
            return repaired
        raise


def blackening_history_config(
    *,
    only_validated: bool,
    queue: str | VideoSegmentValidationNull = None,
) -> OutsideFrameBlackeningHistoryConfigData:
    resolved_queue = queue if queue is not None else get_celery_ffmpeg_media_queue()
    return OutsideFrameBlackeningConfig(
        only_validated=bool(only_validated),
        queue=_validate_blackening_queue(resolved_queue),
    ).to_dict()


def is_outside_frame_blackening_history(
    history: VideoProcessingHistory,
) -> bool:
    history_record = cast(_VideoProcessingHistoryRecord, history)
    try:
        return _parse_blackening_history_record(history_record) is not None
    except OutsideFrameBlackeningConfigError:
        config = history_record.config
        if (
            isinstance(config, dict)
            and config.get("kind") == OUTSIDE_FRAME_BLACKENING_KIND
        ):
            logger.error(
                "Malformed outside-frame blackening config on VideoProcessingHistory %s.",
                history_record.pk,
            )
            return True
        return False


def resolve_blackening_run_config(
    *,
    history: VideoProcessingHistory | VideoSegmentValidationNull,
    only_validated: bool,
) -> OutsideFrameBlackeningConfig:
    if history is None:
        return OutsideFrameBlackeningConfig(
            only_validated=bool(only_validated),
            queue=LEGACY_BLACKENING_QUEUE,
        )

    history_record = cast(_VideoProcessingHistoryRecord, history)
    parsed_config = _parse_blackening_history_record(history_record)
    if parsed_config is None:
        raise OutsideFrameBlackeningConfigError(
            f"VideoProcessingHistory {history_record.pk} is not an outside-frame blackening job."
        )
    return parsed_config


__all__ = [
    "LEGACY_BLACKENING_QUEUE",
    "OUTSIDE_FRAME_BLACKENING_KIND",
    "OutsideFrameBlackeningConfig",
    "OutsideFrameBlackeningConfigError",
    "OutsideFrameBlackeningKind",
    "blackening_history_config",
    "is_outside_frame_blackening_history",
    "parse_blackening_history_config",
    "resolve_blackening_run_config",
]

"""Typed, idempotent import of HF electrosurgical measurement series.

The only accepted input format is the lx_dtypes ``ElectrosurgicalMeasurementSeries``
JavaScript Object Notation (JSON) document. It is validated once at this
boundary, bound to the video's recording and its acquisition-supplied
``RecordingSynchronization``, and stored verbatim as ciphertext through the
encrypted storage backend. No sample is converted, rounded, filled or dropped.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from fractions import Fraction
from typing import Final

from django.core.files.base import ContentFile
from django.db import transaction
from lx_dtypes.models.contracts.electrical_video_alignment import (
    ElectricalFrameAggregationProfile,
)
from lx_dtypes.models.contracts.electrosurgical_measurements import (
    ElectrosurgicalMeasurementSeries,
)
from lx_dtypes.models.contracts.video_provenance import RecordingSynchronization
from pydantic import ValidationError

from endoreg_db.models.media.video.electrosurgical_measurement import (
    VideoElectrosurgicalMeasurement,
    electrosurgical_measurement_upload_to,
)
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.schemas.electrosurgical_measurement import (
    channel_descriptors_from_series,
)
from endoreg_db.schemas.persisted_json import VideoFileMetaPayload
from endoreg_db.services.hub.video_provenance import record_video_synchronization
from endoreg_db.utils.file_operations import safe_delete_field_file
from endoreg_db.utils.hashs import get_bytes_hash
from endoreg_db.utils.structured_logging import emit_structured_event

logger = logging.getLogger(__name__)

# Host limit, deliberately far below the contract's 2,000,000-sample ceiling:
# every alignment request decrypts and validates the whole document.
MAX_ELECTROSURGICAL_SOURCE_BYTES: Final = 64 * 1024 * 1024
_INT64_MIN: Final = -(2**63)
_INT64_MAX: Final = 2**63 - 1


class ElectrosurgicalImportError(ValueError):
    """The measurement cannot be imported without violating an invariant."""


@dataclass(frozen=True, slots=True)
class ElectrosurgicalImportResult:
    measurement: VideoElectrosurgicalMeasurement
    created: bool


def validate_series_bytes(payload: bytes) -> ElectrosurgicalMeasurementSeries:
    """Strict contract validation; error text names locations, never values."""
    if len(payload) > MAX_ELECTROSURGICAL_SOURCE_BYTES:
        raise ElectrosurgicalImportError(
            "Electrosurgical measurement document exceeds the host size limit"
        )
    try:
        series = ElectrosurgicalMeasurementSeries.model_validate_json(payload)
    except ValidationError as exc:
        locations = sorted(
            {
                ".".join(str(part) for part in error["loc"]) or "<root>"
                for error in exc.errors()
            }
        )[:10]
        raise ElectrosurgicalImportError(
            "Electrosurgical measurement series failed contract validation at: "
            + ", ".join(locations)
        ) from None
    ticks = series.timestamps.presentation_timestamps
    if ticks[0] < _INT64_MIN or ticks[-1] > _INT64_MAX:
        raise ElectrosurgicalImportError(
            "Electrical timestamps exceed the persisted 64-bit tick range"
        )
    return series


def bound_recording_synchronization(
    video: VideoFile,
) -> RecordingSynchronization | None:
    """Return the anchors persisted by ``record_video_synchronization``."""
    meta = VideoFileMetaPayload.model_validate(video.meta or {})
    if meta.video_frame_provenance is not None:
        return meta.video_frame_provenance.synchronization
    return meta.recording_synchronization


def _assert_series_matches_synchronization(
    series: ElectrosurgicalMeasurementSeries,
    synchronization: RecordingSynchronization,
) -> None:
    timestamps = series.timestamps
    if (
        timestamps.session_id != synchronization.session_id
        or timestamps.clock_id != synchronization.electrical_clock_id
    ):
        raise ElectrosurgicalImportError(
            "Electrical data belongs to a different session or clock"
        )
    if Fraction(timestamps.time_base_num, timestamps.time_base_den) != Fraction(
        synchronization.electrical_time_base_num,
        synchronization.electrical_time_base_den,
    ):
        raise ElectrosurgicalImportError(
            "Electrical timestamp units differ from the synchronized device clock"
        )


def import_electrosurgical_measurement(
    video_id: int,
    *,
    series_payload: bytes,
    profile: ElectricalFrameAggregationProfile,
    synchronization: RecordingSynchronization | None = None,
) -> ElectrosurgicalImportResult:
    """Persist one series for one video; repeated identical imports are no-ops.

    ``synchronization`` is recorded through the existing acquisition service when
    supplied; otherwise anchors must already be bound to the recording. The same
    source hash with a different document or profile is rejected, never merged.
    """
    series = validate_series_bytes(series_payload)
    payload_sha256 = get_bytes_hash(series_payload)
    profile_json = profile.model_dump(mode="json")

    if synchronization is not None:
        video = VideoFile.objects.get(pk=video_id)
        record_video_synchronization(video, synchronization)

    with transaction.atomic():
        video = VideoFile.objects.select_for_update().get(pk=video_id)
        recording_hash = str(video.raw_video_hash or "")
        if not recording_hash:
            raise ElectrosurgicalImportError(
                "The video has no recording content identity to bind to"
            )
        bound = bound_recording_synchronization(video)
        if bound is None:
            raise ElectrosurgicalImportError(
                "Recording synchronization anchors are required before import"
            )
        if bound.source_content_hash != recording_hash:
            raise ElectrosurgicalImportError(
                "Recording synchronization belongs to a different recording"
            )
        _assert_series_matches_synchronization(series, bound)

        existing = VideoElectrosurgicalMeasurement.objects.filter(
            video_id=video.pk, source_sha256=series.source_sha256
        ).first()
        if existing is not None:
            if (
                existing.payload_sha256 != payload_sha256
                or existing.aggregation_profile != profile_json
                or existing.recording_content_hash != recording_hash
            ):
                _log(
                    "conflict",
                    video_id=video.pk,
                    measurement_id=existing.pk,
                    source_sha256=series.source_sha256,
                    payload_sha256=payload_sha256,
                    level=logging.WARNING,
                )
                raise ElectrosurgicalImportError(
                    "This measurement source is already imported with a different "
                    "document, profile or recording; conflicting re-import rejected"
                )
            _log(
                "unchanged",
                video_id=video.pk,
                measurement_id=existing.pk,
                source_sha256=series.source_sha256,
                payload_sha256=payload_sha256,
            )
            return ElectrosurgicalImportResult(measurement=existing, created=False)

        ticks = series.timestamps.presentation_timestamps
        measurement = VideoElectrosurgicalMeasurement(
            video=video,
            payload_sha256=payload_sha256,
            source_sha256=series.source_sha256,
            recording_content_hash=recording_hash,
            schema_version=series.schema_version,
            parser_id=series.parser_id,
            parser_version=series.parser_version,
            session_id=series.timestamps.session_id,
            electrical_clock_id=series.timestamps.clock_id,
            time_base_num=series.timestamps.time_base_num,
            time_base_den=series.timestamps.time_base_den,
            sample_count=len(ticks),
            first_sample_timestamp=ticks[0],
            last_sample_timestamp=ticks[-1],
            channels=channel_descriptors_from_series(series).model_dump(mode="json")[
                "channels"
            ],
            aggregation_profile=profile_json,
        )
        # The encrypted backend writes ciphertext atomically; the row commits
        # only after the artifact exists, and a failed commit removes it.
        measurement.source_file.name = measurement.source_file.storage.save(
            electrosurgical_measurement_upload_to(
                measurement, f"{payload_sha256}.json"
            ),
            ContentFile[bytes](series_payload),
        )
        try:
            with transaction.atomic():
                measurement.save()
        except Exception:
            safe_delete_field_file(measurement.source_file, missing_ok=True)
            _log(
                "failed",
                video_id=video.pk,
                source_sha256=series.source_sha256,
                payload_sha256=payload_sha256,
                level=logging.ERROR,
            )
            raise
    _log(
        "imported",
        video_id=video.pk,
        measurement_id=measurement.pk,
        source_sha256=series.source_sha256,
        payload_sha256=payload_sha256,
        sample_count=len(ticks),
        channel_count=len(series.channels),
    )
    return ElectrosurgicalImportResult(measurement=measurement, created=True)


def _log(
    status: str,
    *,
    video_id: int,
    source_sha256: str,
    payload_sha256: str,
    measurement_id: int | None = None,
    sample_count: int | None = None,
    channel_count: int | None = None,
    level: int = logging.INFO,
) -> None:
    """Correlate identities only; sample values never reach the logs."""
    emit_structured_event(
        logger,
        "electrosurgical_measurement_import",
        level=level,
        status=status,
        video_id=video_id,
        measurement_id=measurement_id,
        source_sha256=source_sha256,
        payload_sha256=payload_sha256,
        sample_count=sample_count,
        channel_count=channel_count,
    )


__all__ = [
    "MAX_ELECTROSURGICAL_SOURCE_BYTES",
    "ElectrosurgicalImportError",
    "ElectrosurgicalImportResult",
    "bound_recording_synchronization",
    "import_electrosurgical_measurement",
    "validate_series_bytes",
]

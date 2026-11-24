"""Import one lx_dtypes electrosurgical measurement series for one video.

Usage::

    python manage.py import_electrosurgical_measurements \
        --video-id 42 \
        --series series.json \
        --profile profile.json \
        [--synchronization synchronization.json]

``--series`` is an ``ElectrosurgicalMeasurementSeries`` document, ``--profile``
an ``ElectricalFrameAggregationProfile`` and ``--synchronization`` an optional
acquisition-supplied ``RecordingSynchronization``. Re-running with identical
inputs is a no-op; conflicting inputs fail without changes.
"""

from __future__ import annotations

import json
from argparse import ArgumentParser
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from lx_dtypes.models.contracts.electrical_video_alignment import (
    ElectricalFrameAggregationProfile,
)
from lx_dtypes.models.contracts.video_provenance import RecordingSynchronization
from pydantic import ValidationError

from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.electrical.measurement_import import (
    MAX_ELECTROSURGICAL_SOURCE_BYTES,
    ElectrosurgicalImportError,
    import_electrosurgical_measurement,
)

_SMALL_DOCUMENT_BYTES = 1024 * 1024


def _read_bounded(path: Path, limit: int, label: str) -> bytes:
    if not path.is_file():
        raise CommandError(f"{label} file does not exist")
    if path.stat().st_size > limit:
        raise CommandError(f"{label} file exceeds the size limit")
    return path.read_bytes()


class Command(BaseCommand):
    help = (
        "Import an lx_dtypes ElectrosurgicalMeasurementSeries JSON file for one "
        "video, bound to its recording synchronization anchors."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--video-id", type=int, required=True)
        parser.add_argument("--series", type=Path, required=True)
        parser.add_argument("--profile", type=Path, required=True)
        parser.add_argument("--synchronization", type=Path, default=None)

    def handle(self, *args: object, **options: object) -> None:
        video_id = options["video_id"]
        series_path = options["series"]
        profile_path = options["profile"]
        synchronization_path = options["synchronization"]
        if not isinstance(video_id, int) or not isinstance(series_path, Path):
            raise CommandError("--video-id and --series are required")
        if not isinstance(profile_path, Path):
            raise CommandError("--profile is required")
        try:
            profile = ElectricalFrameAggregationProfile.model_validate_json(
                _read_bounded(profile_path, _SMALL_DOCUMENT_BYTES, "Profile")
            )
            synchronization = (
                RecordingSynchronization.model_validate_json(
                    _read_bounded(
                        synchronization_path, _SMALL_DOCUMENT_BYTES, "Synchronization"
                    )
                )
                if isinstance(synchronization_path, Path)
                else None
            )
        except ValidationError as exc:
            raise CommandError(
                f"Invalid profile or synchronization document ({exc.error_count()} errors)"
            ) from None
        series_payload = _read_bounded(
            series_path, MAX_ELECTROSURGICAL_SOURCE_BYTES, "Series"
        )
        try:
            result = import_electrosurgical_measurement(
                video_id,
                series_payload=series_payload,
                profile=profile,
                synchronization=synchronization,
            )
        except VideoFile.DoesNotExist:
            raise CommandError("Video not found") from None
        except (ElectrosurgicalImportError, ValueError) as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(
            json.dumps(
                {
                    "status": "imported" if result.created else "unchanged",
                    "video_id": video_id,
                    "measurement_id": result.measurement.pk,
                    "source_sha256": result.measurement.source_sha256,
                    "sample_count": result.measurement.sample_count,
                },
                sort_keys=True,
            )
        )

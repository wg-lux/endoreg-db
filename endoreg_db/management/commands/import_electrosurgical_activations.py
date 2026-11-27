"""Import activation summaries with explicit source clock and recording anchors."""

from __future__ import annotations

import json
from argparse import ArgumentParser
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from lx_dtypes.models.contracts.electrosurgical_activations import (
    MAX_ACTIVATION_CSV_BYTES,
)
from lx_dtypes.models.contracts.video_provenance import RecordingSynchronization
from pydantic import BaseModel, Field, ValidationError

from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.schemas.electrosurgical_activation import ActivationSourceFormat
from endoreg_db.services.electrical.activation_storage import import_activation_export


class ImportOptions(BaseModel):
    video_id: int = Field(gt=0)
    source: Path
    source_format: ActivationSourceFormat
    source_timezone: str
    synchronization: Path
    max_timing_error_seconds: Decimal = Field(gt=0, allow_inf_nan=False)


def _read(path: Path, limit: int) -> bytes:
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise CommandError("Import input exceeds its size limit")
    return content


class Command(BaseCommand):
    help = "Import per-activation CSV/XLSX summaries using explicit UTC Unix-microsecond synchronization anchors."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--video-id", type=int, required=True)
        parser.add_argument("--source", type=Path, required=True)
        parser.add_argument(
            "--source-format", choices=list(ActivationSourceFormat), required=True
        )
        parser.add_argument("--source-timezone", required=True)
        parser.add_argument("--synchronization", type=Path, required=True)
        parser.add_argument("--max-timing-error-seconds", required=True)

    def handle(self, *args: object, **options: object) -> None:
        try:
            args_ = ImportOptions.model_validate(options)
            sync = RecordingSynchronization.model_validate_json(
                _read(args_.synchronization, 1024 * 1024)
            )
            result = import_activation_export(
                args_.video_id,
                content=_read(args_.source, MAX_ACTIVATION_CSV_BYTES),
                source_format=args_.source_format,
                source_timezone=args_.source_timezone,
                synchronization=sync,
                max_timing_error_seconds=args_.max_timing_error_seconds,
            )
        except (ValidationError, ValueError, OSError, VideoFile.DoesNotExist) as exc:
            raise CommandError(
                "Activation import rejected; verify source format, timezone, recording anchors and tolerance"
            ) from exc
        self.stdout.write(
            json.dumps(
                {
                    "status": "imported" if result.created else "unchanged",
                    "activation_log_id": result.activation_log.pk,
                    "source_sha256": result.activation_log.source_sha256,
                }
            )
        )

"""Local operator entrypoint for restoring a Full Export."""

import csv
import json
from argparse import ArgumentParser
from dataclasses import asdict
from zipfile import BadZipFile

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError
from pydantic import BaseModel

from endoreg_db.models.administration.center.center import Center
from endoreg_db.services.datasets.annotation_import import import_annotation_export
from endoreg_db.services.video_storage.contracts import VideoStorageNormalizationError


class ImportOptions(BaseModel):
    source: str
    center_key: str
    apply: bool


class Command(BaseCommand):
    help = "Verify and restore a Full Export folder or ZIP into a local center."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "source", help="Export folder or ZIP inside the encrypted runtime root"
        )
        parser.add_argument("--center-key", required=True)
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Confirm this is an authorized anonymized export and restore it. Default verifies only.",
        )

    def handle(self, *args: object, **options: object) -> None:
        try:
            parsed = ImportOptions.model_validate(options)
            center = Center.get_by_center_key(parsed.center_key)
            result = import_annotation_export(
                parsed.source, center=center, apply=parsed.apply
            )
        except Center.DoesNotExist as error:
            raise CommandError("Destination center key was not found") from error
        except IntegrityError as error:
            raise CommandError(
                "A conflicting database record prevented publication"
            ) from error
        except (
            BadZipFile,
            csv.Error,
            OSError,
            ValueError,
            VideoStorageNormalizationError,
        ) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(json.dumps({"applied": parsed.apply, **asdict(result)}))

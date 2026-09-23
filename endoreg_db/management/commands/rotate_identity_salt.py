from __future__ import annotations

import json
import logging
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError
from django.core.management.base import BaseCommand, CommandError, CommandParser

from endoreg_db.config.secret_keyring import (
    configured_identity_keyring,
    read_private_file,
)
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.models.administration.person.examiner.examiner import Examiner
from endoreg_db.services.secret_rotation.identity import (
    ReviewedIdentity,
    migrate_identity_group,
    salt_fingerprint,
    ReviewedExaminer,
    migrate_reviewed_examiner,
)
from endoreg_db.utils.structured_logging import emit_structured_event

logger = logging.getLogger(__name__)


class ReviewedIdentities(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    identities: tuple[ReviewedIdentity, ...]
    examiners: tuple[ReviewedExaminer, ...] = ()


class Command(BaseCommand):
    help = "Migrate identity salts online without changing patient or examination primary keys."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--apply", action="store_true")
        parser.add_argument(
            "--review-file",
            type=Path,
            help="Private YAML with reviewed original identities for erased records.",
        )

    def handle(self, *args: object, **options: object) -> None:
        ring = configured_identity_keyring()
        if ring is None:
            raise CommandError(
                "An identity salt manifest must be explicitly configured"
            )
        reviewed: dict[int, ReviewedIdentity] = {}
        examiners: tuple[ReviewedExaminer, ...] = ()
        path = options.get("review_file")
        if path is not None:
            if not isinstance(path, (Path, str)):
                raise CommandError("review_file must be a private file path")
            try:
                payload = ReviewedIdentities.model_validate(
                    yaml.safe_load(
                        read_private_file(Path(path), limit=16 * 1024 * 1024)
                    )
                )
            except (ValueError, ValidationError, yaml.YAMLError):
                raise CommandError(
                    "Invalid reviewed identity file; input values are not logged"
                ) from None
            reviewed = {item.sensitive_meta_id: item for item in payload.identities}
            examiners = payload.examiners
            if len(reviewed) != len(payload.identities):
                raise CommandError("Reviewed identity records must be unique")
            if len({item.examiner_id for item in examiners}) != len(examiners):
                raise CommandError("Reviewed examiner records must be unique")
        active = salt_fingerprint(ring.active)
        migrated = blocked = rows = 0
        groups: set[str] = set()
        for row in (
            SensitiveMeta.objects.filter(external_id__isnull=True)
            .exclude(patient_hash__isnull=True)
            .exclude(patient_hash="")
            .exclude(identity_salt_fingerprint=active)
            .select_related("center")
            .iterator()
        ):
            if str(row.patient_hash) in groups:
                continue
            groups.add(str(row.patient_hash))
            try:
                count = migrate_identity_group(
                    row, reviewed=reviewed, apply=options.get("apply") is True
                )
                if count == 0:
                    blocked += 1
                else:
                    rows += count
                    migrated += 1
            except ValueError as exc:
                blocked += 1
                emit_structured_event(
                    logger,
                    "identity_rotation_blocked",
                    level=logging.ERROR,
                    sensitive_meta_id=int(row.pk),
                    detail=str(exc),
                )
        examiner_failures = 0
        for examiner in examiners:
            try:
                migrate_reviewed_examiner(examiner, apply=options.get("apply") is True)
            except (ValueError, Examiner.DoesNotExist) as exc:
                examiner_failures += 1
                emit_structured_event(
                    logger,
                    "examiner_rotation_blocked",
                    level=logging.ERROR,
                    examiner_id=examiner.examiner_id,
                    detail=str(exc),
                )
        pending_examiners = Examiner.objects.exclude(
            identity_salt_fingerprint=active
        ).count()
        self.stdout.write(
            json.dumps(
                {
                    "patient_groups": migrated,
                    "metadata_rows": rows,
                    "blocked_groups": blocked,
                    "applied": options.get("apply") is True,
                    "pending_examiners": pending_examiners,
                    "examiner_failures": examiner_failures,
                },
                sort_keys=True,
            )
        )
        if blocked or examiner_failures or pending_examiners:
            raise CommandError(
                "Migration incomplete: review missing identities, collisions and linked examination evidence"
            )
        self.stdout.write(
            "Patient migration finished. Examiner mappings, replicas and historical audit records require separate retirement review."
        )

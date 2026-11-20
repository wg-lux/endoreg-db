from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError
from django.core.management.base import BaseCommand, CommandError, CommandParser

from endoreg_db.config.secret_keyring import (
    read_private_file,
)
from endoreg_db.services.secret_rotation.identity import (
    ReviewedIdentity,
    ReviewedExaminer,
)
from endoreg_db.services.secret_rotation.migration import rotate_identity_salt


class ReviewedIdentities(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    identities: tuple[ReviewedIdentity, ...]
    examiners: tuple[ReviewedExaminer, ...] = ()


class Command(BaseCommand):
    help = "Migrate identity salts online without changing patient or examination primary keys."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--compromised", action="store_true")
        parser.add_argument("--expected-active-fingerprint")
        parser.add_argument(
            "--allow-partial",
            action="store_true",
            help="With --apply, commit independently verified groups even if other records remain blocked.",
        )
        parser.add_argument(
            "--review-file",
            type=Path,
            help="Private YAML with reviewed original identities for erased records.",
        )

    def handle(self, *args: object, **options: object) -> None:
        apply = options.get("apply") is True
        allow_partial = options.get("allow_partial") is True
        if allow_partial and not apply:
            raise CommandError("--allow-partial requires --apply")
        self._migrate(options)

    def _migrate(self, options: dict[str, object]) -> None:
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
        expected = options.get("expected_active_fingerprint")
        if expected is not None and not isinstance(expected, str):
            raise CommandError("Expected active fingerprint must be a string")
        try:
            result = rotate_identity_salt(
                apply=options.get("apply") is True,
                allow_partial=options.get("allow_partial") is True,
                compromised=options.get("compromised") is True,
                expected_active_fingerprint=expected,
                reviewed=reviewed,
                examiners=examiners,
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(json.dumps(asdict(result), sort_keys=True))
        if result.incomplete:
            raise CommandError(
                "Migration incomplete: review missing identities, collisions and linked examination evidence"
            )
        self.stdout.write(
            "Patient migration finished. Source recognition, replicas and historical audit records require separate retirement review."
        )

"""Atomically migrate all identity hashes; --dry-run performs no database writes."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import yaml
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import DatabaseError

from endoreg_db.config.secret_keyring import read_private_file
from endoreg_db.management.commands.rotate_identity_salt import ReviewedIdentities
from endoreg_db.services.secret_rotation.legacy_hashes import migrate_legacy_hashes


class Command(BaseCommand):
    help = (
        "Atomically upgrade the entire identity inventory to UTF-8 HMAC-SHA-256. "
        "Use --dry-run to inspect hash diffs without writes. Erased metadata and "
        "substituted examiner names require the private reviewed-source YAML on every run."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument(
            "--review-file",
            type=Path,
            help="Absolute private YAML path, using the rotate_identity_salt review schema.",
        )

    def handle(self, *args: object, **options: object) -> None:
        batch_size = options.get("batch_size", 500)
        if (
            not isinstance(batch_size, int)
            or isinstance(batch_size, bool)
            or batch_size < 1
        ):
            raise CommandError("--batch-size must be a positive integer")
        payload = ReviewedIdentities(identities=())
        path = options.get("review_file")
        try:
            if path is not None:
                if not isinstance(path, (str, Path)):
                    raise ValueError("Invalid review file path")
                payload = ReviewedIdentities.model_validate(
                    yaml.safe_load(
                        read_private_file(Path(path), limit=16 * 1024 * 1024)
                    )
                )
            reviewed = {item.sensitive_meta_id: item for item in payload.identities}
            if len(reviewed) != len(payload.identities):
                raise ValueError("Duplicate reviewed metadata identifiers")
            result = migrate_legacy_hashes(
                dry_run=options.get("dry_run") is True,
                batch_size=batch_size,
                reviewed=reviewed,
                examiners=payload.examiners,
            )
        except (
            ValueError,
            OSError,
            yaml.YAMLError,
            ImproperlyConfigured,
            DatabaseError,
        ):
            # Validation and database exceptions can contain original patient data.
            raise CommandError(
                "Identity migration failed. Check keyring, centers, "
                "reviewed originals, identity links and collisions. Source values are not logged."
            ) from None
        report = asdict(result)
        if not result.dry_run:
            report.pop("diffs")
        self.stdout.write(json.dumps(report, ensure_ascii=False, sort_keys=True))

"""Explicitly reconcile the pinned legacy colorectal indication relationships."""

from __future__ import annotations

import yaml
from pydantic import BaseModel

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import DatabaseError
from lx_dtypes.terminology.terminology_loader import get_terminology_service
from lx_dtypes.terminology.terminology_service import TerminologyError
from endoreg_db.services.reference_data.catalog import catalog_snapshot
from endoreg_db.services.reference_data.catalog_migration import (
    apply_catalog_migration,
    migration_spec,
    plan_catalog_migration,
)


class Command(BaseCommand):
    help = "Reconcile the pinned legacy colorectal indication links in one transaction."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--format", choices=("json", "yml"), default="json")
        parser.add_argument(
            "--dry-run", action="store_true", help="Show changes without writing them."
        )

    def handle(self, *args: str, **options: object) -> None:
        def render(result: BaseModel) -> None:
            if options["format"] == "yml":
                self.stdout.write(
                    yaml.safe_dump(
                        result.model_dump(mode="json", by_alias=True), sort_keys=False
                    )
                )
            else:
                self.stdout.write(result.model_dump_json(indent=2, by_alias=True))

        try:
            spec = migration_spec()
            package = get_terminology_service().load(spec.module, spec.version)
            snapshot = catalog_snapshot(package)
            if options["dry_run"]:
                plan = plan_catalog_migration(snapshot)
                render(plan)
                if not plan.can_apply:
                    raise CommandError("Catalogue migration contains blockers")
                return
            result = apply_catalog_migration(snapshot)
            render(result)
        except (
            ValueError,
            ValidationError,
            DatabaseError,
            OSError,
            TerminologyError,
        ) as exc:
            raise CommandError(
                f"Catalogue migration failed without partial database changes: {exc}"
            ) from exc

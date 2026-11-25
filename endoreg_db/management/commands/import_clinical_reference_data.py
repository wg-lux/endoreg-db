"""Validate and reconcile a selected clinical package before any ORM writes."""

from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import CommandError, CommandParser
from lx_dtypes.terminology.terminology_service import TerminologyError
from lx_dtypes.packages import load_package, load_registered_package
from lx_dtypes.models.contracts.package_snapshot import build_package_snapshot

from endoreg_db.management.reference_catalog_command import ReconciliationCommand

from endoreg_db.services.reference_data.clinical_projection import (
    clinical_snapshot,
    import_clinical_references,
    plan_clinical_reference_import,
)


class Command(ReconciliationCommand):
    help = "Project an exact registered clinical package; dry-run reports legacy conflicts."

    def add_arguments(self, parser: CommandParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--config-path",
            type=Path,
            help="Validate an exact local package manifest before host projection.",
        )

    def handle(self, *args: str, **options: object) -> None:
        module, version = options["module"], options["module_version"]
        if not isinstance(module, str) or not isinstance(version, str):
            raise CommandError("Explicit module and module version are required")
        try:
            config_path = options.get("config_path")
            if config_path is not None and not isinstance(config_path, Path):
                raise CommandError("config_path must be a package manifest path")
            package = (
                load_package(config_path)
                if config_path is not None
                else load_registered_package(module, version=version)
            )
            if package.config.name != module or package.config.version != version:
                raise CommandError(
                    "Resolved clinical package differs from the requested identity"
                )
            snapshot = clinical_snapshot(package)
            package_snapshot = build_package_snapshot(package)
            plan = plan_clinical_reference_import(
                snapshot,
                adopt_existing=options["adopt_existing"] is True,
                package_snapshot=package_snapshot,
            )

            if not plan.can_import:
                self.render_plan(plan, as_yaml=options["format"] == "yml")
                raise CommandError(
                    "Clinical reference reconciliation has conflicts; no rows were changed"
                )
            if options["dry_run"] is not True:
                plan = import_clinical_references(
                    snapshot,
                    adopt_existing=options["adopt_existing"] is True,
                    package_snapshot=package_snapshot,
                )
            self.render_plan(plan, as_yaml=options["format"] == "yml")
        except (ValueError, ValidationError, OSError, TerminologyError) as exc:
            raise CommandError(
                "Clinical reference import failed validation; no partial import committed"
            ) from exc

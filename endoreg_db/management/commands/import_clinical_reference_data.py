"""Validate and reconcile a selected clinical package before any ORM writes."""

from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError, CommandParser
from lx_dtypes.terminology.terminology_service import TerminologyError
from lx_dtypes.packages import load_package, load_registered_package
from lx_dtypes.models.contracts.package_snapshot import build_package_snapshot
import yaml

from endoreg_db.services.reference_data.clinical_projection import (
    clinical_snapshot,
    import_clinical_references,
    plan_clinical_reference_import,
)


class Command(BaseCommand):
    help = "Project an exact registered clinical package; dry-run reports legacy conflicts."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--module", required=True)
        parser.add_argument("--module-version", required=True)
        parser.add_argument(
            "--config-path",
            type=Path,
            help="Validate an exact local package manifest before host projection.",
        )
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--format", choices=("json", "yml"), default="json")
        parser.add_argument(
            "--adopt-existing",
            action="store_true",
            help="Bind equivalent legacy rows without rewriting their clinical meaning.",
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

            def render() -> None:
                if options["format"] == "yml":
                    self.stdout.write(
                        yaml.safe_dump(
                            plan.model_dump(mode="json"),
                            allow_unicode=True,
                            sort_keys=False,
                        )
                    )
                else:
                    self.stdout.write(plan.model_dump_json(indent=2))

            if not plan.can_import:
                render()
                raise CommandError(
                    "Clinical reference reconciliation has conflicts; no rows were changed"
                )
            if options["dry_run"] is not True:
                plan = import_clinical_references(
                    snapshot,
                    adopt_existing=options["adopt_existing"] is True,
                    package_snapshot=package_snapshot,
                )
            render()
        except (ValueError, ValidationError, OSError, TerminologyError) as exc:
            raise CommandError(
                "Clinical reference import failed validation; no partial import committed"
            ) from exc

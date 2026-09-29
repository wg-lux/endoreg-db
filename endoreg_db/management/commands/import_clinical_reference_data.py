"""Validate and reconcile a selected clinical package before any ORM writes."""

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError, CommandParser
from lx_dtypes.terminology.terminology_loader import get_terminology_service
from lx_dtypes.terminology.terminology_service import TerminologyError
import yaml

from endoreg_db.services.clinical_reference_data import (
    clinical_snapshot,
    import_clinical_references,
    plan_clinical_reference_import,
)


class Command(BaseCommand):
    help = "Project an exact registered clinical package; dry-run reports legacy conflicts."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--module", required=True)
        parser.add_argument("--module-version", required=True)
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
            package = get_terminology_service().load(module, version)
            if package.config.name != module or package.config.version != version:
                raise CommandError(
                    "Resolved clinical package differs from the requested identity"
                )
            snapshot = clinical_snapshot(package)
            plan = plan_clinical_reference_import(
                snapshot, adopt_existing=options["adopt_existing"] is True
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
                    snapshot, adopt_existing=options["adopt_existing"] is True
                )
            render()
        except (ValueError, ValidationError, OSError, TerminologyError) as exc:
            raise CommandError(
                "Clinical reference import failed validation; no partial import committed"
            ) from exc

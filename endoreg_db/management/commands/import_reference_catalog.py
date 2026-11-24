"""Validate and reconcile a selected reference catalogue package before any ORM writes."""

from django.core.exceptions import ValidationError
from django.db import DatabaseError
from django.core.management.base import BaseCommand, CommandError, CommandParser
from lx_dtypes.terminology.terminology_loader import get_terminology_service
from lx_dtypes.terminology.terminology_service import TerminologyError
from lx_dtypes.models.contracts.reference_catalog import ReferenceKind
from pydantic import TypeAdapter
import yaml

from endoreg_db.services.reference_data.catalog import (
    CatalogPlan,
    catalog_snapshot,
    import_reference_catalog,
    plan_reference_catalog,
    reference_catalog_transaction,
    reuse_current_catalog,
    select_catalog,
)


class Command(BaseCommand):
    help = "Project an exact registered reference catalogue package; dry-run reports legacy conflicts."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--module", required=True)
        parser.add_argument("--module-version", required=True)
        parser.add_argument(
            "--record-type",
            dest="record_types",
            action="append",
            choices=[kind.value for kind in ReferenceKind],
            default=[],
        )
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--format", choices=("json", "yml"), default="json")
        parser.add_argument(
            "--adopt-existing",
            action="store_true",
            help="Bind equivalent legacy rows without rewriting their clinical meaning.",
        )
        parser.add_argument(
            "--verify",
            action="store_true",
            help="Compare every catalogue row even when an identical receipt exists.",
        )

    def handle(self, *args: str, **options: object) -> None:
        module, version = options["module"], options["module_version"]
        if not isinstance(module, str) or not isinstance(version, str):
            raise CommandError("Explicit module and module version are required")
        try:
            package = get_terminology_service().load(module, version)
            if package.config.name != module or package.config.version != version:
                raise CommandError(
                    "Resolved reference catalogue package differs from the requested identity"
                )
            kinds = TypeAdapter(list[str]).validate_python(
                options["record_types"], strict=True
            )
            snapshot = select_catalog(
                catalog_snapshot(package), {ReferenceKind(kind) for kind in kinds}
            )

            def render(plan: CatalogPlan) -> None:
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

            if options["dry_run"] is not True and options["verify"] is not True:
                with reference_catalog_transaction():
                    reused = reuse_current_catalog(snapshot)
                if reused is not None:
                    render(reused)
                    return
            plan = plan_reference_catalog(
                snapshot, adopt_existing=options["adopt_existing"] is True
            )
            if not plan.can_import:
                render(plan)
                raise CommandError(
                    "Reference catalogue reconciliation has conflicts; no rows were changed"
                )
            if options["dry_run"] is not True:
                plan = import_reference_catalog(
                    snapshot, adopt_existing=options["adopt_existing"] is True
                )
            render(plan)
        except DatabaseError as exc:
            raise CommandError(
                "Reference catalogue database operation failed; verify database migrations before retrying"
            ) from exc
        except (ValueError, ValidationError, OSError, TerminologyError) as exc:
            raise CommandError(
                "Reference catalogue import failed validation; no partial import committed"
            ) from exc

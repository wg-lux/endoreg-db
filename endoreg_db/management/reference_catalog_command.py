"""Compatibility command boundary for typed catalogue projections."""

from typing import ClassVar

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandParser
from lx_dtypes.models.contracts.reference_catalog import (
    ReferenceKind,
    TERMINOLOGY_REFERENCE_MODELS,
)
from lx_dtypes.terminology.terminology_loader import hydrate_shipped_terminology
from endoreg_db.config.settings.dataloader import APPLICATION_PACKAGE


class ReferenceCatalogCommand(BaseCommand):
    record_types: ClassVar[tuple[ReferenceKind, ...]] = ()
    default_module: ClassVar[str] = APPLICATION_PACKAGE.knowledge_base_module
    default_version: ClassVar[str] = APPLICATION_PACKAGE.knowledge_base_version
    help = "Load a pinned reference catalogue with its complete dependencies."

    def add_arguments(self, parser: CommandParser) -> None:
        module, version = self.default_module, self.default_version
        clinical_kinds = set(TERMINOLOGY_REFERENCE_MODELS) - {
            ReferenceKind.UNIT,
            ReferenceKind.INFORMATION_SOURCE,
            ReferenceKind.INFORMATION_SOURCE_TYPE,
        }
        clinical_selection = (
            module == APPLICATION_PACKAGE.knowledge_base_module
            and bool(clinical_kinds.intersection(self.record_types))
        )
        parser.add_argument(
            "--module",
            default=None if clinical_selection else module,
            required=clinical_selection,
        )
        parser.add_argument(
            "--module-version",
            default=None if clinical_selection else version,
            required=clinical_selection,
        )
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--format", choices=("json", "yml"), default="json")
        parser.add_argument("--verbose", action="store_true")
        parser.add_argument(
            "--verify",
            action="store_true",
            help="Compare every catalogue row even when an identical receipt exists.",
        )

    def handle(self, *args: str, **options: object) -> None:
        # Provision the governed registry explicitly at the bootstrap boundary.
        # A dry-run requires an already provisioned registry and writes nothing.
        if options["dry_run"] is not True:
            hydrate_shipped_terminology()
        # Compatibility commands adopt only exact equivalents, never overwrite.
        call_command(
            "import_reference_catalog",
            module=options["module"],
            module_version=options["module_version"],
            dry_run=options["dry_run"],
            format=options["format"],
            record_types=[kind.value for kind in self.record_types],
            adopt_existing=True,
            verify=options["verify"],
            stdout=self.stdout,
            stderr=self.stderr,
        )

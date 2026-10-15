"""Compatibility command boundary for typed catalogue projections."""

from typing import ClassVar

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandParser
from lx_dtypes.models.contracts.reference_catalog import ReferenceKind
from lx_dtypes.terminology.terminology_loader import hydrate_shipped_terminology


class ReferenceCatalogCommand(BaseCommand):
    record_types: ClassVar[tuple[ReferenceKind, ...]] = ()
    default_module: ClassVar[str] = "endoreg_reference"
    default_version: ClassVar[str] = "1.0.0"
    help = "Load a pinned reference catalogue with its complete dependencies."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--module", default=self.default_module)
        parser.add_argument("--module-version", default=self.default_version)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--format", choices=("json", "yml"), default="json")
        parser.add_argument("--verbose", action="store_true")

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
            stdout=self.stdout,
            stderr=self.stderr,
        )

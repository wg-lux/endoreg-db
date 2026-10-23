"""Provision the selected versioned catalogue; study activation is separate."""

from django.core.management import call_command
from django.core.management.base import CommandError, CommandParser
from lx_dtypes.terminology.terminology_loader import hydrate_shipped_terminology

from endoreg_db.management.reference_catalog_command import ReferenceCatalogCommand


class Command(ReferenceCatalogCommand):
    help = "Provision endoreg_reference@1.0.0, or an explicitly selected registered catalogue."

    def add_arguments(self, parser: CommandParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--reconcile-legacy",
            action="store_true",
            help="Reconcile the known colorectal indication upgrade before base-data import.",
        )

    def handle(self, *args: str, **options: object) -> None:
        if options["reconcile_legacy"] is not True:
            return super().handle(*args, **options)
        if (
            options["module"] != self.default_module
            or options["module_version"] != self.default_version
        ):
            raise CommandError("--reconcile-legacy requires endoreg_reference@1.0.0")
        if options["dry_run"] is not True:
            hydrate_shipped_terminology()
        call_command(
            "migrate_reference_catalog",
            dry_run=options["dry_run"],
            format=options["format"],
            stdout=self.stdout,
            stderr=self.stderr,
        )

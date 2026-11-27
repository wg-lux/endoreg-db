"""Provision the selected versioned catalogue; study activation is separate."""

from django.core.management.base import CommandParser

from endoreg_db.management.reference_catalog_command import ReferenceCatalogCommand


class Command(ReferenceCatalogCommand):
    help = "Provision endoreg_reference@2.0.0 with terminology-owned clinical dependencies."

    def add_arguments(self, parser: CommandParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--reconcile-legacy",
            action="store_true",
            help="Compatibility flag: reconcile the current catalogue without rewriting clinical definitions.",
        )

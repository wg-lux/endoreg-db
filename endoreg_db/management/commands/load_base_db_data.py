"""Provision the selected versioned catalogue; study activation is separate."""

from endoreg_db.management.reference_catalog_command import ReferenceCatalogCommand


class Command(ReferenceCatalogCommand):
    help = "Provision endoreg_reference@1.0.0, or an explicitly selected registered catalogue."

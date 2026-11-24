"""Reconcile current application data and pinned terminology without legacy rewrites."""

from endoreg_db.management.reference_catalog_command import ReferenceCatalogCommand


class Command(ReferenceCatalogCommand):
    help = "Reconcile endoreg_reference@2.0.0; conflicting clinical meanings require review."

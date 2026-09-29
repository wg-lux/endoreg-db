"""Compatibility entry point for an optional versioned reference catalogue."""

from endoreg_db.management.reference_catalog_command import ReferenceCatalogCommand
from lx_dtypes.models.contracts.reference_catalog import ReferenceKind


class Command(ReferenceCatalogCommand):
    default_module = "endoreg_workforce"
    record_types = (ReferenceKind.PROFESSION,)

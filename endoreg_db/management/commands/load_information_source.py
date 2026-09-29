"""Compatibility entry point for the versioned lx-dtypes catalogue."""

from endoreg_db.management.reference_catalog_command import ReferenceCatalogCommand
from lx_dtypes.models.contracts.reference_catalog import ReferenceKind


class Command(ReferenceCatalogCommand):
    record_types = (
        ReferenceKind.INFORMATION_SOURCE_TYPE,
        ReferenceKind.INFORMATION_SOURCE,
    )

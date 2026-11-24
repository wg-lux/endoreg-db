"""Compatibility entry point for the versioned lx-dtypes catalogue."""

from endoreg_db.management.reference_catalog_command import ReferenceCatalogCommand
from lx_dtypes.models.contracts.reference_catalog import ReferenceKind


class Command(ReferenceCatalogCommand):
    record_types = (
        ReferenceKind.NUMERIC_VALUE_DISTRIBUTION,
        ReferenceKind.SINGLE_CATEGORICAL_VALUE_DISTRIBUTION,
        ReferenceKind.MULTIPLE_CATEGORICAL_VALUE_DISTRIBUTION,
        ReferenceKind.DATE_VALUE_DISTRIBUTION,
    )

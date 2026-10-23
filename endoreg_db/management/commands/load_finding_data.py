"""Compatibility entry point for the versioned lx-dtypes catalogue."""

from endoreg_db.management.reference_catalog_command import ReferenceCatalogCommand
from lx_dtypes.models.contracts.reference_catalog import ReferenceKind


class Command(ReferenceCatalogCommand):
    record_types = (
        ReferenceKind.FINDING_INTERVENTION_TYPE,
        ReferenceKind.FINDING_INTERVENTION,
        ReferenceKind.FINDING_TYPE,
        ReferenceKind.FINDING_CLASSIFICATION_CHOICE,
        ReferenceKind.FINDING_CLASSIFICATION_TYPE,
        ReferenceKind.FINDING_CLASSIFICATION,
        ReferenceKind.FINDING,
    )

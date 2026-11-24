"""Compatibility entry point for the versioned lx-dtypes catalogue."""

from endoreg_db.management.reference_catalog_command import ReferenceCatalogCommand
from lx_dtypes.models.contracts.reference_catalog import ReferenceKind


class Command(ReferenceCatalogCommand):
    record_types = (
        ReferenceKind.MEDICATION,
        ReferenceKind.MEDICATION_INDICATION_TYPE,
        ReferenceKind.MEDICATION_INTAKE_TIME,
        ReferenceKind.MEDICATION_SCHEDULE,
        ReferenceKind.MEDICATION_INDICATION,
    )

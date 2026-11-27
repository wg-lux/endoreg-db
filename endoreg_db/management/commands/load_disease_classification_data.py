"""Compatibility entry point for typed reference catalogue provisioning."""

from endoreg_db.management.reference_catalog_command import ReferenceCatalogCommand
from lx_dtypes.models.contracts.reference_catalog import ReferenceKind


class Command(ReferenceCatalogCommand):
    record_types = (ReferenceKind.DISEASE_CLASSIFICATION,)

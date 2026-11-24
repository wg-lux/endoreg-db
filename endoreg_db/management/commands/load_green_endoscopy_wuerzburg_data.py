"""Compatibility entry point for an optional versioned reference catalogue."""

from endoreg_db.management.reference_catalog_command import ReferenceCatalogCommand
from lx_dtypes.models.contracts.reference_catalog import ReferenceKind


class Command(ReferenceCatalogCommand):
    default_module = "endoreg_green_endoscopy"
    record_types = (
        ReferenceKind.EMISSION_FACTOR,
        ReferenceKind.RESOURCE,
        ReferenceKind.WASTE,
        ReferenceKind.MATERIAL,
        ReferenceKind.PRODUCT_GROUP,
        ReferenceKind.TRANSPORT_ROUTE,
        ReferenceKind.PRODUCT,
        ReferenceKind.REFERENCE_PRODUCT,
        ReferenceKind.CENTER_WASTE,
        ReferenceKind.CENTER_RESOURCE,
        ReferenceKind.PRODUCT_MATERIAL,
        ReferenceKind.PRODUCT_WEIGHT,
    )

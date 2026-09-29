"""Lossless natural-key representation for catalogue reconciliation and export."""

from typing import cast
from math import isnan

from django.db import models
from lx_dtypes.models.contracts.reference_catalog import (
    CATALOG_RECORD_TYPES,
    CatalogIdentity,
    CatalogRecordBase,
    ReferenceCatalogPayload,
    ReferenceKind,
)

from endoreg_db.services.reference_catalog_models import CATALOG_MODELS


def row_identity(row: models.Model) -> CatalogIdentity:
    name: object = getattr(row, "name")
    version: object = getattr(row, "version", None)
    return CatalogIdentity.model_validate({"name": name, "version": version})


def export_reference_row(kind: ReferenceKind, row: models.Model) -> CatalogRecordBase:
    contract = CATALOG_RECORD_TYPES[kind]
    data: dict[str, object] = {"record_type": kind}
    for field in contract.model_fields:
        if field == "record_type":
            continue
        value: object = getattr(row, field)
        if field in contract.many_relations:
            manager = cast(models.Manager[models.Model], value)
            identities = [row_identity(related) for related in manager.all()]
            data[field] = sorted(
                identities, key=lambda key: (key.name, key.version or 0)
            )
        elif field in contract.relation_targets:
            if value is not None and not isinstance(value, models.Model):
                raise TypeError(f"Invalid reference relation {kind}.{field}")
            data[field] = row_identity(value) if value is not None else None
        else:
            # ProductWeight._has_weight defines NaN as an absent measurement.
            # Preserve that legacy meaning in the finite, JSON-safe contract.
            if (
                kind == ReferenceKind.PRODUCT_WEIGHT
                and field in {"measured", "verified", "manufacturer"}
                and isinstance(value, float)
                and isnan(value)
            ):
                value = None
            data[field] = value
    return contract.model_validate(data)


def export_reference_catalog() -> ReferenceCatalogPayload:
    """Only explicit reference models; never patient or captured clinical records."""
    records = [
        export_reference_row(kind, row).model_dump(mode="json", by_alias=True)
        for kind, model in CATALOG_MODELS.items()
        for row in model._base_manager.order_by("pk")
    ]
    return ReferenceCatalogPayload.model_validate({"records": records})

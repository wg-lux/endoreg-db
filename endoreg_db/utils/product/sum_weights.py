from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, TYPE_CHECKING, TypeAlias, cast

if TYPE_CHECKING:
    from endoreg_db.models.administration.product.product_material import (
        ProductMaterial,
    )
    from endoreg_db.models.other.unit import Unit

NoProductMaterialValue: TypeAlias = None
ProductMaterialMetric: TypeAlias = tuple[float, "Unit | NoProductMaterialValue"]


class _ProductMaterialWeightSource(Protocol):
    unit: "Unit"
    quantity: float


def sum_weights(
    product_materials: Iterable["ProductMaterial"],
) -> ProductMaterialMetric:
    weight = 0.0
    reference_unit: Unit | NoProductMaterialValue = None
    for product_material in product_materials:
        material = cast(_ProductMaterialWeightSource, product_material)
        if not reference_unit:
            reference_unit = material.unit
        else:
            assert reference_unit == material.unit, "ProductMaterial units do not match"
        weight += material.quantity

    return weight, reference_unit

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, TYPE_CHECKING, TypeAlias, cast

if TYPE_CHECKING:
    from endoreg_db.models.administration.product.product_material import (
        ProductMaterial,
    )
    from endoreg_db.models.other.unit import Unit

ProductMaterialMetric: TypeAlias = tuple[float, "Unit | None"]


class _ProductMaterialEmissionSource(Protocol):
    unit: "Unit"

    def get_emission(self) -> ProductMaterialMetric: ...


def sum_emissions(
    product_materials: Iterable["ProductMaterial"],
) -> ProductMaterialMetric:
    emission = 0.0
    reference_unit: Unit | None = None
    for product_material in product_materials:
        material = cast(_ProductMaterialEmissionSource, product_material)
        if not reference_unit:
            reference_unit = material.unit
        else:
            assert reference_unit == material.unit, "ProductMaterial units do not match"
        em_value, emission_unit = material.get_emission()
        assert reference_unit == emission_unit, "ProductMaterial units do not match"
        emission += em_value

    return emission, reference_unit

from endoreg_db.models.administration.product.product_material import ProductMaterial


from typing import List


def sum_emissions(product_materials:List["ProductMaterial"]):
    # sum up the emissions
    emission = 0
    reference_unit = None
    for product_material in product_materials:
        if not reference_unit:
            reference_unit = product_material.unit
        else:
            assert reference_unit == product_material.unit, "ProductMaterial units do not match"
        em_value, emission_unit = product_material.get_emission()
        assert reference_unit == emission_unit, "ProductMaterial units do not match"
        emission += em_value

    return emission, reference_unit
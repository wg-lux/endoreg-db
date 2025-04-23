from endoreg_db.models.administration.product.product_material import ProductMaterial


from typing import List


def sum_weights(product_materials: List["ProductMaterial"]):
    # sum up the weights
    weight = 0
    reference_unit = None
    for product_material in product_materials:
        if not reference_unit:
            reference_unit = product_material.unit
        else:
            assert reference_unit == product_material.unit, "ProductMaterial units do not match"
        weight += product_material.quantity

    return weight, reference_unit
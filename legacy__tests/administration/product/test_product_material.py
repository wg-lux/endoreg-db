from django.test import TestCase
from logging import getLogger

from endoreg_db.models.administration.product.product import Product
from endoreg_db.models.administration.product.product_material import ProductMaterial
from endoreg_db.models.other.unit import Unit


logger = getLogger(__name__)
logger.debug("Starting test for Patient model")

from ...helpers.data_loader import (
    load_unit_data,
    load_distribution_data,
    load_center_data,
    load_examination_data,
    load_green_endoscopy_wuerzburg_data
)



class ProductMaterialModelTest(TestCase):
    def setUp(self):
        load_unit_data()
        load_examination_data()
        load_distribution_data()
        load_center_data()
        load_green_endoscopy_wuerzburg_data()

    def test_has_material(self):
        """
        Test if all product materials have a material.
        """
        from endoreg_db.models import ProductMaterial
        product_materials = ProductMaterial.objects.all()
        for product_material in product_materials:
            self.assertIsInstance(product_material, ProductMaterial)
            self.assertIsNotNone(product_material.material)

    def test_has_unit(self):
        """
        Test if all product materials have a unit.
        """
        from endoreg_db.models import ProductMaterial
        product_materials = ProductMaterial.objects.all()
        for product_material in product_materials:
            self.assertIsNotNone(product_material.unit)

    def test_has_emission_factor(self):
        """
        Test if all product materials have an emission factor.
        """
        product_materials = ProductMaterial.objects.all()
        for product_material in product_materials:
            emission, _unit = product_material.get_emission()
            self.assertIsInstance(emission, float)
            self.assertIsInstance(_unit, Unit)

    def has_product(self):
        """
        Test if all product materials have a product.
        """
        product_materials = ProductMaterial.objects.all()
        for product_material in product_materials:
            self.assertIsInstance(product_material.product, Product)

    def test_has_quantity(self):
        """
        Test if all product materials have a quantity.
        """
        product_materials = ProductMaterial.objects.all()
        for product_material in product_materials:
            self.assertIsInstance(product_material.quantity, float)
        
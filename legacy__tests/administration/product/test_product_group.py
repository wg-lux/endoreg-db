from django.test import TestCase
from logging import getLogger

from endoreg_db.models import (
    ProductGroup,
    ReferenceProduct
)

logger = getLogger(__name__)
logger.debug("Starting test for Patient model")

from ...helpers.data_loader import (
    load_unit_data,
    load_distribution_data,
    load_center_data,
    load_examination_data,
    load_green_endoscopy_wuerzburg_data
)



class ProductGroupModelTest(TestCase):
    def setUp(self):
        load_unit_data()
        load_examination_data()
        load_distribution_data()
        load_center_data()
        load_green_endoscopy_wuerzburg_data()

    def test_product_groups_have_products(self):
        """
        Test if all product groups have products.
        """
        product_groups = ProductGroup.objects.all()
        for product_group in product_groups:
            self.assertIsInstance(product_group, ProductGroup)
            products = product_group.get_products()
            self.assertTrue(products.exists(), f"Product group {product_group} has no products.")
    
    def test_product_group_has_reference_product(self):
        """
        Test if all product groups have a reference product.
        """
        product_groups = ProductGroup.objects.all()
        for product_group in product_groups:
            self.assertIsInstance(product_group, ProductGroup)
            reference_product = product_group.get_reference_product()
            self.assertIsInstance(reference_product, ReferenceProduct, f"Product group {product_group} has no reference product.")
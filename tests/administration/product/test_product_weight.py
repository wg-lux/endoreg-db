from django.test import TestCase
from typing import Protocol, cast
from logging import getLogger

from endoreg_db.models import Product, Unit, ProductWeight

logger = getLogger(__name__)
logger.debug("Starting test for Patient model")

from ...helpers.data_loader import (
    load_unit_data,
    load_distribution_data,
    load_center_data,
    load_examination_data,
    load_green_endoscopy_wuerzburg_data,
)


class ProductWeightModelTest(TestCase):
    _unit: Unit

    _product_weights: list[ProductWeight]

    def setUp(self):
        load_unit_data()
        load_examination_data()
        load_distribution_data()
        load_center_data()
        load_green_endoscopy_wuerzburg_data()

    def test_product_weight_has_product(self):
        """
        Test if all product weights have a product.
        """
        product_weights = ProductWeight.objects.all()
        for product_weight in product_weights:
            product_weight_like = cast(_ProductWeightLike, product_weight)
            self.assertIsInstance(product_weight, ProductWeight)
            self.assertIsInstance(product_weight_like.product, Product)

    def test_product_weight_has_unit(self):
        """
        Test if all product weights have a unit.
        """
        product_weights = ProductWeight.objects.all()
        for product_weight in product_weights:
            product_weight_like = cast(_ProductWeightLike, product_weight)
            self.assertIsInstance(product_weight_like.unit, Unit)
            self.assertIsInstance(product_weight_like.get_weight(), float)
            self.assertIsInstance(product_weight_like.get_weight_source(), str)

    def test_weight_has_weight(self):
        """
        Test if all product weights have a weight.
        """
        product_weights = ProductWeight.objects.all()
        for product_weight in product_weights:
            product_weight_like = cast(_ProductWeightLike, product_weight)
            self.assertIsInstance(product_weight_like.get_weight(), float)
            self.assertIsInstance(product_weight_like.get_weight_source(), str)


class _ProductWeightLike(Protocol):
    product: Product
    unit: Unit

    def get_weight(self) -> float: ...
    def get_weight_source(self) -> str: ...

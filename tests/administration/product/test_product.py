from django.test import TestCase
from typing import Protocol, cast
from endoreg_db.models import (
    Product,
    ProductGroup,
    TransportRoute,
)
from logging import getLogger

from endoreg_db.models.administration.product.product_material import ProductMaterial
from endoreg_db.models.administration.product.reference_product import ReferenceProduct


class _QuerySetLike(Protocol):
    def first(self) -> object | None: ...


class _ProductLike(Protocol):
    name: str
    product_group: ProductGroup
    transport_route: TransportRoute
    reference_products: _QuerySetLike
    product_product_materials: _QuerySetLike

    def get_product_weight(self) -> tuple[object, object] | None: ...
    def get_package_weight(self) -> tuple[object, object] | None: ...


logger = getLogger(__name__)

logger.debug("Starting test for Patient model")

from ...helpers.data_loader import (
    load_unit_data,
    load_distribution_data,
    load_center_data,
    load_examination_data,
    load_green_endoscopy_wuerzburg_data,
)


class ProductModelTest(TestCase):
    # Test suite works on pre-loaded fixture models created in shared data loader.
    products: list[Product]

    def setUp(self):
        load_unit_data()
        load_examination_data()
        load_distribution_data()
        load_center_data()
        load_green_endoscopy_wuerzburg_data()

    def test_product_creation(self):
        """
        Test if the green_endoscopy_wuerzburg products are created correctly.
        Test if all products have a product group
        Test if all products have a transport route.
        Test if all products have a reference product.
        Test if all products have a product material.
        """
        products = Product.objects.all()

        for product in products:
            product_like = cast(_ProductLike, product)
            self.assertIsInstance(product, Product)
            self.assertIsInstance(product_like.name, str)
            self.assertIsInstance(product_like.product_group, ProductGroup)
            self.assertIsInstance(product_like.transport_route, TransportRoute)
            self.assertIsInstance(
                product_like.reference_products.first(), ReferenceProduct
            )
            self.assertIsNotNone(
                product_like.product_product_materials.first(), ProductMaterial
            )

    def test_product_material_weight(self):
        """
        Test if the green_endoscopy_wuerzburg products are created correctly.
        We should be able to calculate the product weight for all products.
        """
        products = Product.objects.all()

        for product in products:
            # check if the product has a product weight
            product_weight_result = cast(_ProductLike, product).get_product_weight()
            if product_weight_result is None:
                raise self.failureException(
                    "product.get_product_weight() must return a metric tuple"
                )

            product_weight, _unit = product_weight_result
            self.assertIsNotNone(product_weight)

    def test_package_weight(self):
        """
        Test if the green_endoscopy_wuerzburg products are created correctly.
        We should be able to calculate the package weight for all products.
        """
        products = Product.objects.all()

        for product in products:
            # check if the product has a package weight
            package_weight_result = cast(_ProductLike, product).get_package_weight()
            if package_weight_result is None:
                raise self.failureException(
                    "product.get_package_weight() must return a metric tuple"
                )

            package_weight, _unit = package_weight_result
            self.assertIsNotNone(package_weight)

from typing import List
from django.test import TestCase
from django.db import models
from endoreg_db.models import (
    Product,
    ProductGroup,
    TransportRoute,
    Examination, PatientExamination,
    ExaminationIndication,
    PatientExaminationIndication,
    Center, # Import Center
    PatientLabSample,
)
from datetime import date
from logging import getLogger
from pathlib import Path
import random

from endoreg_db.models.administration.product.product_material import ProductMaterial
from endoreg_db.models.administration.product.reference_product import ReferenceProduct


logger = getLogger(__name__)

logger.debug("Starting test for Patient model")

from ...helpers.data_loader import (
    load_unit_data,
    load_distribution_data,
    load_center_data,
    load_examination_data,
    load_examination_indication_data,
    load_gender_data,
    load_lab_value_data,
    load_green_endoscopy_wuerzburg_data
)

from ...helpers.default_objects import (
    generate_patient,
    get_random_default_examination,
    get_random_default_examination_indication,
    get_default_egd_pdf,
    get_random_gender,
)

class ProductModelTest(TestCase):
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
            self.assertIsInstance(product, Product)
            self.assertIsInstance(product.name, str)
            self.assertIsInstance(product.product_group, ProductGroup)
            self.assertIsInstance(product.transport_route, TransportRoute)
            self.assertIsInstance(product.reference_products.first(), ReferenceProduct)
            self.assertIsNotNone(product.product_product_materials.first(), ProductMaterial)
    

    def test_product_material_weight(self):
        """
        Test if the green_endoscopy_wuerzburg products are created correctly.
        We should be able to calculate the product weight for all products.
        """
        products = Product.objects.all()

        for product in products:
            # check if the product has a product weight
            product_weight, _unit = product.get_product_weight()
            self.assertIsNotNone(product_weight)

    def test_package_weight(self):
        """
        Test if the green_endoscopy_wuerzburg products are created correctly.
        We should be able to calculate the package weight for all products.
        """
        products = Product.objects.all()

        for product in products:
            # check if the product has a package weight
            package_weight, _unit = product.get_package_weight()
            self.assertIsNotNone(package_weight)

from django.test import TestCase
from logging import getLogger

from endoreg_db.models import (
    ProductGroup,
    ReferenceProduct
)

logger = getLogger(__name__)
logger.debug("Starting test for Patient model")

from ...helpers.data_loader import (
    load_qualification_data
)



class QualificationModelTest(TestCase):
    def setUp(self):
        """
        Prepares test data for each test by loading qualification-related fixtures.
        """
        load_qualification_data()


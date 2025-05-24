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
        Prepares qualification-related test data before each test method is executed.
        """
        load_qualification_data()


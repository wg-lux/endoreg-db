from django.test import TestCase
from logging import getLogger

from endoreg_db.models import (
    ProductGroup,
    ReferenceProduct
)

logger = getLogger(__name__)
logger.debug("Starting test for Patient model")

from ...helpers.data_loader import (
    load_shift_data
)



class ShiftModelTest(TestCase):
    def setUp(self):
        """
        Prepares test data for Shift model tests by loading predefined shift data before each test.
        """
        load_shift_data()


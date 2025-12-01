from django.test import TestCase
from logging import getLogger

from endoreg_db.models import (
    Label,
)
from endoreg_db.models.label import LabelType, LabelSet

logger = getLogger(__name__)
logger.debug("Starting test for Patient model")

from ..helpers.data_loader import (
    load_ai_model_label_data,
)



class PolypClassificationLabelsTest(TestCase):
    def setUp(self):
        load_ai_model_label_data()

    def test_get_paris_labels(self):
        """
        Get all Paris labels.
        """
        paris_labels = LabelSet.objects.get(name="endoscopy_paris_classification")
        db_paris_label_names = [_.name for _ in paris_labels.labels.all()]

        expected_names = [
            "paris_1s",
            "paris_1p",
            "paris_2a",
            "paris_2b",
            "paris_2c",
            "paris_3",
        ]

        for name in expected_names:
            self.assertIn(name, db_paris_label_names, f"Label {name} not found in database.")
            logger.debug(f"Label {name} found in database.")


    def test_get_nice_label_types(self):
        """
        Get all NICE label types.
        """
        nice_labels = LabelSet.objects.get(name="endoscopy_nice_classification")
        db_nice_label_names = [_.name for _ in nice_labels.labels.all()]

        expected_names = [
            "nice_1",
            "nice_2a",
            "nice_2b",
            "nice_3",
        ]

        for name in expected_names:
            self.assertIn(name, db_nice_label_names, f"Label {name} not found in database.")
            logger.debug(f"Label {name} found in database.")

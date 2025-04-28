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



class LabelModelTest(TestCase):
    def setUp(self):
        load_ai_model_label_data()

    def test_label_outside_exists(self):
        """
        Test if all labels have a label outside.
        """
        outside_label = Label.get_outside_label()
        self.assertIsInstance(outside_label, Label)

    def test_label_low_quality_exists(self):
        """
        Test if all labels have a label
        """
        low_quality_label = Label.get_low_quality_label()
        self.assertIsInstance(low_quality_label, Label)

    def test_label_have_types(self):
        """
        Test if all labels have a label type.
        """
        labels = Label.objects.all()
        for label in labels:
            self.assertIsInstance(label, Label)
            self.assertIsInstance(label.label_type, LabelType)

class LabelSetModelTest(TestCase):
    def setUp(self):
        load_ai_model_label_data()

    def test_label_set_have_labels(self):
        """
        Test if all label sets have labels.
        """
        label_sets = LabelSet.objects.all()
        for label_set in label_sets:
            self.assertIsInstance(label_set, LabelSet)
            self.assertTrue(label_set.labels.exists(), f"Label set {label_set} has no labels.")

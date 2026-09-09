from django.test import TestCase
from logging import getLogger
from typing import Protocol, cast

from endoreg_db.models import (
    Label,
)
from endoreg_db.models.label import LabelType, LabelSet

logger = getLogger(__name__)
logger.debug("Starting test for Patient model")


class LabelModelTest(TestCase):
    label_type: LabelType
    outside_label: Label
    low_quality_label: Label

    class _LabelLike(Protocol):
        label_type: LabelType

    @classmethod
    def setUpTestData(cls):
        cls.label_type = LabelType.objects.create(name="classification")
        cls.outside_label = Label.objects.create(
            name="outside",
            label_type=cls.label_type,
        )
        cls.low_quality_label = Label.objects.create(
            name="low_quality",
            label_type=cls.label_type,
        )

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
        labels = Label.objects.filter(
            name__in=[self.outside_label.name, self.low_quality_label.name]
        ).order_by("name")
        for label in labels:
            self.assertIsInstance(label, Label)
            typed_label = cast(LabelModelTest._LabelLike, label)
            self.assertIsInstance(typed_label.label_type, LabelType)


class LabelSetModelTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.label_type = LabelType.objects.create(name="classification")
        cls.outside_label = Label.objects.create(
            name="outside",
            label_type=cls.label_type,
        )
        cls.low_quality_label = Label.objects.create(
            name="low_quality",
            label_type=cls.label_type,
        )
        cls.label_set = LabelSet.objects.create(name="default", version=1)
        cls.label_set.labels.set([cls.outside_label, cls.low_quality_label])

    def test_label_set_have_labels(self):
        """
        Test if all label sets have labels.
        """
        label_sets = LabelSet.objects.all()
        for label_set in label_sets:
            self.assertIsInstance(label_set, LabelSet)
            self.assertTrue(
                label_set.labels.exists(), f"Label set {label_set} has no labels."
            )

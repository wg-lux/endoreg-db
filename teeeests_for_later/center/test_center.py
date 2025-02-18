from django.test import TestCase
from endoreg_db.models import Center

class CenterTestCase(TestCase):
    def setUp(self):
        Center.objects.create(name="Center A")
        Center.objects.create(name="Center B")

    def test_created(self):
        """Animals that can speak are correctly identified"""
        _a = Center.objects.get(name="Center A")
        _b = Center.objects.get(name="Center B")

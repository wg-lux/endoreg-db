from django.test import TestCase
from endoreg_db.models import Material

# class MaterialModelTest(TestCase):
#     def setUp(self):
#         # Create a Material instance for testing
#         self.material = Material.objects.create(
#             name="test_material",
#             name_de="Testmaterial",
#             name_en="Test material",
#             abbreviation="TM",
#             description="This is a Test Material"
#         )
        
#     def test_material_creation(self):
#         """Test if the material is created correctly."""
#         self.assertEqual(self.material.name, "test_material")
#         self.assertEqual(self.material.abbreviation, "TM")
#         self.assertEqual(self.material.description, "This is a Test Material")
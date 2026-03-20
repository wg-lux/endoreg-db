"""
Tests for tag-based filtering of requirement sets in requirement guidance.
"""

from __future__ import annotations
import pytest
from django.test import TestCase


from endoreg_db.models import Center, ExaminationRequirementSet, Tag
from endoreg_db.models.administration.person import Patient
from endoreg_db.models.medical.examination import Examination, ExaminationType
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.requirement.requirement_set import (
    RequirementSet,
    RequirementSetType,
)
from endoreg_db.services import lookup_service as ls


@pytest.mark.django_db
class TestLookupTagFiltering(TestCase):
    """Test requirement set filtering by user role tags."""

    def setUp(self):
        """Set up test data with tagged requirement sets."""
        # Get or create tags (they may already exist from management command)
        self.tag_gastro, _ = Tag.objects.get_or_create(name="Gastroenterologist")
        self.tag_student, _ = Tag.objects.get_or_create(name="Student")
        self.tag_professor, _ = Tag.objects.get_or_create(name="Professor")

        # Create requirement set type (unique per test)
        self.req_set_type, _ = RequirementSetType.objects.get_or_create(
            name="test_type", defaults={"description": "Test requirement set type"}
        )

        # Create requirement sets
        self.req_set_gastro = RequirementSet.objects.create(
            name="Advanced Colonoscopy QA",
            requirement_set_type=self.req_set_type,
            description="Advanced requirements for specialists",
        )
        self.req_set_gastro.tags.add(self.tag_gastro, self.tag_professor)

        self.req_set_student = RequirementSet.objects.create(
            name="Basic Endoscopy",
            requirement_set_type=self.req_set_type,
            description="Basic requirements for students",
        )
        self.req_set_student.tags.add(self.tag_student)

        self.req_set_all = RequirementSet.objects.create(
            name="General Requirements",
            requirement_set_type=self.req_set_type,
            description="General requirements for all users",
        )
        # No tags - available to all

        # Create examination and patient examination
        # Get or create a center
        center, _ = Center.objects.get_or_create(
            name="Test Center",
            defaults={"display_name": "Test Center for Tag Filtering"},
        )

        # Create patient
        patient = Patient.objects.create(
            first_name="Test", last_name="Patient", dob="2000-01-01", center=center
        )

        # Create examination
        exam_type, _ = ExaminationType.objects.get_or_create(name="Colonoscopy")

        self.examination = Examination.objects.create(
            name="Test Examination", description="Test examination for tag filtering"
        )
        self.examination.examination_types.add(exam_type)

        # Create patient examination
        self.pe = PatientExamination.objects.create(
            patient=patient, examination=self.examination
        )

        # Link all requirement sets to examination via ExaminationRequirementSet
        for idx, req_set in enumerate(
            [self.req_set_gastro, self.req_set_student, self.req_set_all]
        ):
            ers, _ = ExaminationRequirementSet.objects.get_or_create(
                name=f"test_ers_{idx}", defaults={"enabled_by_default": True}
            )
            ers.examinations.add(self.examination)
            req_set.reqset_exam_links.add(ers)

    def test_no_tag_filter_returns_all(self):
        """Without tag filtering, all linked requirement sets should be returned."""
        result = ls.requirement_sets_for_patient_exam(self.pe, user_tags=None)
        self.assertEqual(result.count(), 3)

    def test_gastro_tag_filter(self):
        """Gastroenterologist tag should return only tagged requirement sets."""
        result = ls.requirement_sets_for_patient_exam(
            self.pe, user_tags=["Gastroenterologist"]
        )
        self.assertEqual(result.count(), 1)
        self.assertIn(self.req_set_gastro, result)

    def test_student_tag_filter(self):
        """Student tag should return only student-tagged requirement sets."""
        result = ls.requirement_sets_for_patient_exam(self.pe, user_tags=["Student"])
        self.assertEqual(result.count(), 1)
        self.assertIn(self.req_set_student, result)

    def test_multiple_tags_filter(self):
        """Multiple tags should return requirement sets matching any tag (OR logic)."""
        result = ls.requirement_sets_for_patient_exam(
            self.pe, user_tags=["Gastroenterologist", "Student"]
        )
        self.assertEqual(result.count(), 2)
        self.assertIn(self.req_set_gastro, result)
        self.assertIn(self.req_set_student, result)

    def test_nonexistent_tag_returns_empty(self):
        """Non-existent tag should return no requirement sets."""
        result = ls.requirement_sets_for_patient_exam(
            self.pe, user_tags=["NonExistentRole"]
        )
        self.assertEqual(result.count(), 0)

    def test_professor_tag_matches_gastro_set(self):
        """Professor tag should match the gastro requirement set (has both tags)."""
        result = ls.requirement_sets_for_patient_exam(self.pe, user_tags=["Professor"])
        self.assertEqual(result.count(), 1)
        self.assertIn(self.req_set_gastro, result)

    def test_empty_tags_list_returns_all(self):
        """Empty tags list should be treated same as None - return all requirement sets."""
        result = ls.requirement_sets_for_patient_exam(self.pe, user_tags=[])
        self.assertEqual(result.count(), 3)

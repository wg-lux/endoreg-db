from datetime import date

from django.test import TestCase

from endoreg_db.models import (
    Requirement,
    RequirementType,
    RequirementOperator,
    Patient,
    PatientMedication,
    Medication,
    Unit,
)


class RequirementQuerysetEvaluationTests(TestCase):
    def setUp(self):
        self.unit = Unit.objects.create(name="milligram", abbreviation="mg")
        self.req_type_pm, _ = RequirementType.objects.get_or_create(name="patient_medication")
        self.op_match_any, _ = RequirementOperator.objects.get_or_create(name="models_match_any")
        self.patient = Patient.objects.create(
            first_name="John",
            last_name="Doe",
            dob=date(1980, 1, 1),
        )

    def _create_medication(self, name: str) -> Medication:
        return Medication.objects.create(name=name, default_unit=self.unit)

    def _create_patient_medication(self, medication: Medication) -> PatientMedication:
        return PatientMedication.objects.create(patient=self.patient, medication=medication)

    def test_requirement_with_links_no_operators_queryset_returns_false(self):
        medication = self._create_medication("med-no-ops")
        patient_med = self._create_patient_medication(medication)

        requirement = Requirement.objects.create(name="req_no_ops")
        requirement.requirement_types.add(self.req_type_pm) # type: ignore
        requirement.medications.add(medication) # type: ignore

        qs = PatientMedication.objects.filter(pk__in=[patient_med.pk])
        self.assertFalse(requirement.evaluate(qs, mode="loose"))

    def test_queryset_mode_all_requires_all_items_to_match(self):
        med_target = self._create_medication("med-target")
        med_other = self._create_medication("med-other")

        matching_pm = self._create_patient_medication(med_target)
        non_matching_pm = self._create_patient_medication(med_other)

        requirement = Requirement.objects.create(
            name="req_qs_all",
            string_values="qs_mode=all",
        )
        requirement.requirement_types.add(self.req_type_pm) # type: ignore
        requirement.operators.add(self.op_match_any) # type: ignore
        requirement.medications.add(med_target) # type: ignore

        qs_mixed = PatientMedication.objects.filter(pk__in=[matching_pm.pk, non_matching_pm.pk])
        self.assertFalse(requirement.evaluate(qs_mixed, mode="strict"))

        another_matching_pm = self._create_patient_medication(med_target)
        qs_all_match = PatientMedication.objects.filter(
            pk__in=[matching_pm.pk, another_matching_pm.pk]
        )
        self.assertTrue(requirement.evaluate(qs_all_match, mode="strict"))

    def test_queryset_mode_min_count_requires_threshold(self):
        med_target = self._create_medication("med-threshold")
        med_other = self._create_medication("med-other-threshold")

        pm1 = self._create_patient_medication(med_target)
        pm2 = self._create_patient_medication(med_target)
        pm3 = self._create_patient_medication(med_other)

        requirement = Requirement.objects.create(
            name="req_qs_min_count",
            string_values="qs_mode=min_count,qs_min_count=2",
        )
        requirement.requirement_types.add(self.req_type_pm) # type: ignore
        requirement.operators.add(self.op_match_any) # type: ignore
        requirement.medications.add(med_target) # type: ignore

        qs_two_matching = PatientMedication.objects.filter(pk__in=[pm1.pk, pm2.pk, pm3.pk])
        self.assertTrue(requirement.evaluate(qs_two_matching, mode="loose"))

        qs_one_matching = PatientMedication.objects.filter(pk__in=[pm1.pk, pm3.pk])
        self.assertFalse(requirement.evaluate(qs_one_matching, mode="loose"))

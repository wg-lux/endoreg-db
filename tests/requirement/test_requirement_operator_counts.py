import calendar
from datetime import date, timedelta
from uuid import uuid4

from django.test import TestCase

from endoreg_db.models import (
    Event,
    Patient,
    PatientEvent,
    Requirement,
    RequirementOperator,
    RequirementType,
    Unit,
)


class RequirementOperatorCountTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.operator_names = [
            "models_match_none",
            "models_match_n",
            "models_match_n_or_more",
            "models_match_n_or_less",
            "models_match_count_in_range",
            "models_match_all_in_timeframe",
            "models_match_none_in_timeframe",
            "models_match_n_in_timeframe",
            "models_match_n_or_more_in_timeframe",
            "models_match_n_or_less_in_timeframe",
        ]
        cls.operators = {name: RequirementOperator.objects.get_or_create(name=name)[0] for name in cls.operator_names}
        cls.rt_patient, _ = RequirementType.objects.get_or_create(name="patient")
        cls.rt_patient_event, _ = RequirementType.objects.get_or_create(name="patient_event")
        cls.unit_hours, _ = Unit.objects.get_or_create(name="hours", defaults={"abbreviation": "h"})
        cls.unit_days, _ = Unit.objects.get_or_create(name="days", defaults={"abbreviation": "d"})
        cls.unit_weeks, _ = Unit.objects.get_or_create(name="weeks", defaults={"abbreviation": "w"})
        cls.unit_months, _ = Unit.objects.get_or_create(name="months", defaults={"abbreviation": "mo"})
        cls.unit_years, _ = Unit.objects.get_or_create(name="years", defaults={"abbreviation": "y"})

    def _make_event(self, label: str) -> Event:
        return Event.objects.create(name=f"{label}-{uuid4().hex}")

    def _make_patient(self, label: str) -> Patient:
        return Patient.objects.create(
            first_name=f"{label}-fn",
            last_name=f"{label}-ln",
            dob=date(1980, 1, 1),
        )

    def _create_patient_event(
        self,
        patient: Patient,
        event: Event,
        days_ago: int | None = None,
        *,
        event_date: date | None = None,
    ) -> PatientEvent:
        if event_date is None:
            if days_ago is None:
                raise ValueError("Either days_ago or event_date must be provided.")
            event_date = date.today() - timedelta(days=days_ago)
        return PatientEvent.objects.create(
            patient=patient,
            event=event,
            date_start=event_date,
        )

    def _date_months_ago(self, months: int) -> date:
        today = date.today()
        total_months = today.month - 1 - months
        year = today.year + total_months // 12
        month = total_months % 12 + 1
        day = min(today.day, calendar.monthrange(year, month)[1])
        return date(year, month, day)

    def _date_years_ago(self, years: int) -> date:
        return self._date_months_ago(years * 12)

    def _make_requirement(
        self,
        name: str,
        operator_name: str,
        events: list[Event],
        *,
        numeric_value=None,
        numeric_value_min=None,
        numeric_value_max=None,
        unit: Unit | None = None,
    ) -> Requirement:
        requirement = Requirement.objects.create(
            name=f"{name}-{uuid4().hex}",
            numeric_value=numeric_value,
            numeric_value_min=numeric_value_min,
            numeric_value_max=numeric_value_max,
            unit=unit,
        )
        requirement.requirement_types.add(self.rt_patient, self.rt_patient_event)
        requirement.operators.add(self.operators[operator_name])
        for event in events:
            requirement.events.add(event)
        return requirement

    def _evaluate(self, requirement: Requirement, patient: Patient, *patient_events: PatientEvent) -> bool:
        return requirement.evaluate(patient, *patient_events, mode="strict")

    def test_models_match_none_respects_absence_of_events(self):
        patient = self._make_patient("none")
        target_event = self._make_event("target")
        requirement = self._make_requirement("match-none", "models_match_none", [target_event])

        self.assertTrue(self._evaluate(requirement, patient))

        matching_event = self._create_patient_event(patient, target_event, days_ago=1)
        self.assertFalse(self._evaluate(requirement, patient, matching_event))

    def test_models_match_n_requires_exact_number(self):
        patient = self._make_patient("exact")
        event_alpha = self._make_event("alpha")
        event_beta = self._make_event("beta")
        requirement = self._make_requirement(
            "match-n",
            "models_match_n",
            [event_alpha, event_beta],
            numeric_value=2,
        )

        pe_alpha = self._create_patient_event(patient, event_alpha, days_ago=4)
        pe_beta = self._create_patient_event(patient, event_beta, days_ago=3)
        self.assertTrue(self._evaluate(requirement, patient, pe_alpha, pe_beta))

        self.assertFalse(self._evaluate(requirement, patient, pe_alpha))

    def test_models_match_n_or_more_allows_excess_matches(self):
        patient = self._make_patient("or-more")
        event_alpha = self._make_event("alpha")
        event_beta = self._make_event("beta")
        requirement = self._make_requirement(
            "match-n-or-more",
            "models_match_n_or_more",
            [event_alpha, event_beta],
            numeric_value=1,
        )

        pe_alpha = self._create_patient_event(patient, event_alpha, days_ago=2)
        pe_beta = self._create_patient_event(patient, event_beta, days_ago=1)
        self.assertTrue(self._evaluate(requirement, patient, pe_alpha, pe_beta))

        self.assertFalse(self._evaluate(requirement, patient))

    def test_models_match_n_or_less_enforces_upper_bound(self):
        patient = self._make_patient("or-less")
        event_alpha = self._make_event("alpha")
        event_beta = self._make_event("beta")
        requirement = self._make_requirement(
            "match-n-or-less",
            "models_match_n_or_less",
            [event_alpha, event_beta],
            numeric_value=1,
        )

        pe_alpha = self._create_patient_event(patient, event_alpha, days_ago=2)
        self.assertTrue(self._evaluate(requirement, patient, pe_alpha))

        pe_beta = self._create_patient_event(patient, event_beta, days_ago=1)
        self.assertFalse(self._evaluate(requirement, patient, pe_alpha, pe_beta))

    def test_models_match_count_in_range_respects_bounds(self):
        patient = self._make_patient("range")
        event_alpha = self._make_event("alpha")
        event_beta = self._make_event("beta")
        requirement = self._make_requirement(
            "match-range",
            "models_match_count_in_range",
            [event_alpha, event_beta],
            numeric_value_min=1,
            numeric_value_max=2,
        )

        pe_alpha = self._create_patient_event(patient, event_alpha, days_ago=3)
        self.assertTrue(self._evaluate(requirement, patient, pe_alpha))

        pe_beta = self._create_patient_event(patient, event_beta, days_ago=2)
        self.assertTrue(self._evaluate(requirement, patient, pe_alpha, pe_beta))

        self.assertFalse(self._evaluate(requirement, patient))

    def test_models_match_all_in_timeframe_requires_recent_events(self):
        patient = self._make_patient("all-tf")
        event_alpha = self._make_event("alpha")
        event_beta = self._make_event("beta")
        requirement = self._make_requirement(
            "match-all-tf",
            "models_match_all_in_timeframe",
            [event_alpha, event_beta],
            numeric_value_min=-30,
            numeric_value_max=0,
            unit=self.unit_days,
        )

        pe_alpha_recent = self._create_patient_event(patient, event_alpha, days_ago=5)
        pe_beta_recent = self._create_patient_event(patient, event_beta, days_ago=6)
        self.assertTrue(self._evaluate(requirement, patient, pe_alpha_recent, pe_beta_recent))

        pe_beta_old = self._create_patient_event(patient, event_beta, days_ago=60)
        self.assertFalse(self._evaluate(requirement, patient, pe_alpha_recent, pe_beta_old))

    def test_models_match_none_in_timeframe_fails_on_recent_event(self):
        patient = self._make_patient("none-tf")
        event_alpha = self._make_event("alpha")
        requirement = self._make_requirement(
            "match-none-tf",
            "models_match_none_in_timeframe",
            [event_alpha],
            numeric_value_min=-30,
            numeric_value_max=0,
            unit=self.unit_days,
        )

        old_event = self._create_patient_event(patient, event_alpha, days_ago=45)
        self.assertTrue(self._evaluate(requirement, patient, old_event))

        recent_event = self._create_patient_event(patient, event_alpha, days_ago=3)
        self.assertFalse(self._evaluate(requirement, patient, recent_event))

    def test_models_match_n_in_timeframe_requires_exact_recent_count(self):
        patient = self._make_patient("n-tf")
        event_alpha = self._make_event("alpha")
        event_beta = self._make_event("beta")
        requirement = self._make_requirement(
            "match-n-tf",
            "models_match_n_in_timeframe",
            [event_alpha, event_beta],
            numeric_value=2,
            numeric_value_min=-30,
            numeric_value_max=0,
            unit=self.unit_days,
        )

        pe_alpha_recent = self._create_patient_event(patient, event_alpha, days_ago=4)
        pe_beta_recent = self._create_patient_event(patient, event_beta, days_ago=1)
        self.assertTrue(self._evaluate(requirement, patient, pe_alpha_recent, pe_beta_recent))

        pe_beta_old = self._create_patient_event(patient, event_beta, days_ago=90)
        self.assertFalse(self._evaluate(requirement, patient, pe_alpha_recent, pe_beta_old))

    def test_models_match_n_or_more_in_timeframe_allows_threshold(self):
        patient = self._make_patient("n-more-tf")
        event_alpha = self._make_event("alpha")
        event_beta = self._make_event("beta")
        requirement = self._make_requirement(
            "match-n-more-tf",
            "models_match_n_or_more_in_timeframe",
            [event_alpha, event_beta],
            numeric_value=1,
            numeric_value_min=-30,
            numeric_value_max=0,
            unit=self.unit_days,
        )

        pe_alpha_recent = self._create_patient_event(patient, event_alpha, days_ago=7)
        pe_beta_recent = self._create_patient_event(patient, event_beta, days_ago=2)
        self.assertTrue(self._evaluate(requirement, patient, pe_alpha_recent, pe_beta_recent))

        self.assertFalse(self._evaluate(requirement, patient))

    def test_models_match_n_or_less_in_timeframe_enforces_upper_bound(self):
        patient = self._make_patient("n-less-tf")
        event_alpha = self._make_event("alpha")
        event_beta = self._make_event("beta")
        requirement = self._make_requirement(
            "match-n-less-tf",
            "models_match_n_or_less_in_timeframe",
            [event_alpha, event_beta],
            numeric_value=1,
            numeric_value_min=-30,
            numeric_value_max=0,
            unit=self.unit_days,
        )

        pe_alpha_recent = self._create_patient_event(patient, event_alpha, days_ago=8)
        self.assertTrue(self._evaluate(requirement, patient, pe_alpha_recent))

        pe_beta_recent = self._create_patient_event(patient, event_beta, days_ago=2)
        self.assertFalse(self._evaluate(requirement, patient, pe_alpha_recent, pe_beta_recent))

    # def test_timeframe_unit_hours_supported(self):
    #     patient = self._make_patient("hours")
    #     event_alpha = self._make_event("alpha")
    #     requirement = self._make_requirement(
    #         "match-hours",
    #         "models_match_n_in_timeframe",
    #         [event_alpha],
    #         numeric_value=1,
    #         numeric_value_min=-48,
    #         numeric_value_max=0,
    #         unit=self.unit_hours,
    #     )

    #     within_event = self._create_patient_event(patient, event_alpha, days_ago=1)
    #     self.assertTrue(self._evaluate(requirement, patient, within_event))

    #     outside_event = self._create_patient_event(patient, event_alpha, days_ago=3)
    #     self.assertFalse(self._evaluate(requirement, patient, outside_event))

    def test_timeframe_unit_weeks_supported(self):
        patient = self._make_patient("weeks")
        event_alpha = self._make_event("alpha")
        requirement = self._make_requirement(
            "match-weeks",
            "models_match_all_in_timeframe",
            [event_alpha],
            numeric_value_min=-2,
            numeric_value_max=0,
            unit=self.unit_weeks,
        )

        within_event = self._create_patient_event(patient, event_alpha, days_ago=10)
        self.assertTrue(self._evaluate(requirement, patient, within_event))

        outside_event = self._create_patient_event(patient, event_alpha, days_ago=20)
        self.assertFalse(self._evaluate(requirement, patient, outside_event))

    def test_timeframe_unit_months_supported(self):
        patient = self._make_patient("months")
        event_alpha = self._make_event("alpha")
        requirement = self._make_requirement(
            "match-months",
            "models_match_n_in_timeframe",
            [event_alpha],
            numeric_value=1,
            numeric_value_min=-6,
            numeric_value_max=0,
            unit=self.unit_months,
        )

        recent_date = self._date_months_ago(5)
        within_event = self._create_patient_event(patient, event_alpha, event_date=recent_date)
        self.assertTrue(self._evaluate(requirement, patient, within_event))

        old_date = self._date_months_ago(7)
        outside_event = self._create_patient_event(patient, event_alpha, event_date=old_date)
        self.assertFalse(self._evaluate(requirement, patient, outside_event))

    def test_timeframe_unit_years_supported(self):
        patient = self._make_patient("years")
        event_alpha = self._make_event("alpha")
        requirement = self._make_requirement(
            "match-years",
            "models_match_n_or_more_in_timeframe",
            [event_alpha],
            numeric_value=1,
            numeric_value_min=-1,
            numeric_value_max=0,
            unit=self.unit_years,
        )

        within_event = self._create_patient_event(patient, event_alpha, days_ago=300)
        self.assertTrue(self._evaluate(requirement, patient, within_event))

        outside_event = self._create_patient_event(patient, event_alpha, days_ago=400)
        self.assertFalse(self._evaluate(requirement, patient, outside_event))

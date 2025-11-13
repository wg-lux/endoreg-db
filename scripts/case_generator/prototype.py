"""Quick-and-dirty sandbox for a RequirementSet driven case generator.

This module will evolve into a structured generator that can ingest a
``RequirementSet`` and materialise a patient record (examinations, lab values,
medications, events) satisfying all requirements. For now we only explore the
data structures and formulate an execution plan skeleton.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable, List, Optional

import django
from icecream import ic

# Ensure Django is ready before importing models without clobbering preconfigured settings
DJANGO_SETTINGS_MODULE = os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "config.settings.case_gen",
)
django.setup()

from django.utils import timezone

from endoreg_db.models import (  # noqa: E402
    Center,
    Examination,
    Gender,
    Patient,
    PatientEvent,
    PatientExamination,
    PatientFinding,
    PatientLabValue,
    PatientMedication,
    Requirement,
    RequirementSet,
)


@dataclass
class RequirementPlan:
    """Represents the actions required to satisfy a single Requirement."""

    requirement: Requirement
    operators: List[str] = field(default_factory=list)
    inferred_models: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


@dataclass
class RequirementSetPlan:
    """Hierarchy describing how to satisfy a RequirementSet graph."""

    requirement_set: RequirementSet
    requirement_plans: List[RequirementPlan] = field(default_factory=list)
    linked_set_plans: List["RequirementSetPlan"] = field(default_factory=list)

    def all_requirements(self) -> Iterable[Requirement]:
        for plan in self.requirement_plans:
            yield plan.requirement
        for linked in self.linked_set_plans:
            yield from linked.all_requirements()


def analyse_requirement(requirement: Requirement) -> RequirementPlan:
    """Best-effort inference of the artefacts a requirement will need.

    At this stage we simply capture hints that later steps can translate into
    concrete model factory calls.
    """

    notes: List[str] = []
    inferred_models: List[str] = []

    links = requirement.links
    active = links.active()
    if active.get("lab_values"):
        inferred_models.append("PatientLabValue")
        notes.append("Ensure lab value measurements exist for linked LabValue entries.")
    if active.get("patient_findings") or active.get("findings"):
        inferred_models.append("PatientFinding")
        notes.append("Attach findings to the generated examination.")
    if active.get("patient_events") or active.get("events"):
        inferred_models.append("PatientEvent")
        notes.append("Record patient events matching requirement events.")
    if active.get("patient_medications") or active.get("medications"):
        inferred_models.append("PatientMedication")
        notes.append("Link medication schedules / intake times as required.")
    if active.get("patient_examinations") or active.get("examinations"):
        inferred_models.append("PatientExamination")
        notes.append("Create examination with proper indications/findings.")

    if not inferred_models:
        notes.append("Requirement has no direct links; may rely on operators only.")

    operator_names = list(requirement.operators.values_list("name", flat=True))

    return RequirementPlan(
        requirement=requirement,
        operators=operator_names,
        inferred_models=sorted(set(inferred_models)),
        notes=notes,
    )


def build_requirement_set_plan(req_set: RequirementSet, _visited: Optional[set[int]] = None) -> RequirementSetPlan:
    """Recursively map a RequirementSet graph into a generation plan."""

    visited = _visited or set()
    if req_set.pk in visited:
        return RequirementSetPlan(requirement_set=req_set)
    visited.add(req_set.pk)

    direct_plans = [analyse_requirement(req) for req in req_set.requirements.all()]

    linked_plans: List[RequirementSetPlan] = []
    for linked in req_set.links_to_sets.all():
        linked_plans.append(build_requirement_set_plan(linked, visited))

    return RequirementSetPlan(
        requirement_set=req_set,
        requirement_plans=direct_plans,
        linked_set_plans=linked_plans,
    )


def generate_case_plan(req_set_name: str) -> RequirementSetPlan:
    """Entry-point for the future generator.

    Produces a hierarchical plan capturing all requirements that need to be
    satisfied. Later iterations will translate this plan into concrete ORM
    objects and orchestrate validation loops.
    """

    req_set = RequirementSet.objects.get(name=req_set_name)
    return build_requirement_set_plan(req_set)


def pretty_print_plan(plan: RequirementSetPlan, indent: int = 0) -> None:
    prefix = " " * indent
    set_type = getattr(plan.requirement_set.requirement_set_type, "name", "unspecified")
    ic(prefix + f"RequirementSet: {plan.requirement_set.name} [{set_type}]")
    for requirement_plan in plan.requirement_plans:
        req = requirement_plan.requirement
        ic(prefix + "  Requirement: " + req.name + f" | operators={requirement_plan.operators}")
        if requirement_plan.inferred_models:
            ic(prefix + "    models => " + ", ".join(requirement_plan.inferred_models))
        for note in requirement_plan.notes:
            ic(prefix + "    note: " + note)
    for linked_plan in plan.linked_set_plans:
        pretty_print_plan(linked_plan, indent + 2)


@dataclass
class GenerationContext:
    """Mutable state carrying generated artefacts and audit trail."""

    patient: Optional[Patient] = None
    examinations: List[PatientExamination] = field(default_factory=list)
    findings: List[PatientFinding] = field(default_factory=list)
    lab_values: List[PatientLabValue] = field(default_factory=list)
    events: List[PatientEvent] = field(default_factory=list)
    medications: List[PatientMedication] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def add_note(self, message: str) -> None:
        self.notes.append(message)
        ic(f"[CTX] {message}")

    def ensure_patient(self) -> Patient:
        if self.patient is None:
            raise RuntimeError("Generation context does not have a patient yet.")
        return self.patient

    def ensure_examination(self, examination_model: Optional[Examination] = None) -> PatientExamination:
        if examination_model is not None:
            for existing in self.examinations:
                if existing.examination and existing.examination.pk == examination_model.pk:
                    return existing
            target_name = getattr(examination_model, "name", None)
            if target_name:
                exam = self.ensure_patient().create_examination(
                    examination_name_str=target_name,
                    date_start=timezone.now(),
                    date_end=timezone.now() + timedelta(minutes=30),
                )
            else:
                exam = PatientExamination.objects.create(
                    patient=self.ensure_patient(),
                    examination=examination_model,
                    date_start=timezone.now(),
                    date_end=timezone.now() + timedelta(minutes=30),
                )
        else:
            if self.examinations:
                return self.examinations[0]
            exam = PatientExamination.objects.create(
                patient=self.ensure_patient(),
                date_start=timezone.now(),
                date_end=timezone.now() + timedelta(minutes=30),
            )
        self.examinations.append(exam)
        label = exam.examination.name if exam.examination else "unspecified"
        self.add_note(f"Ensured patient examination '{label}' (id={exam.pk})")
        return exam


def initialize_generation_context() -> GenerationContext:
    """Provision a patient scaffold using available reference data."""

    center = Center.objects.first()
    gender = Gender.objects.first()
    if center is None or gender is None:
        raise RuntimeError("Generator requires seeded Center and Gender records.")

    patient = Patient.objects.create(
        first_name="Auto",
        last_name="Generated",
        dob=date(1970, 1, 1),
        center=center,
        gender=gender,
    )

    context = GenerationContext(patient=patient)
    context.add_note(f"Created patient #{patient.pk} with center={center.name} gender={gender.name}")
    return context


def satisfy_requirement(plan: RequirementPlan, context: GenerationContext) -> None:
    """Placeholder requirement executor that records intended actions."""

    requirement = plan.requirement
    links = requirement.links.active()
    context.add_note(f"Processing requirement '{requirement.name}' with operators={plan.operators}")
    for note in plan.notes:
        context.add_note(f"  hint: {note}")

    patient = context.ensure_patient()

    if plan.inferred_models or links.get("examinations"):
        ensure_examinations_and_findings(context, links)

    if "PatientLabValue" in plan.inferred_models or links.get("lab_values"):
        ensure_lab_values(context, patient, links)

    if "PatientMedication" in plan.inferred_models or links.get("medications"):
        ensure_medications(context, patient, links)

    if "PatientEvent" in plan.inferred_models or links.get("events"):
        ensure_events(context, patient, links)

    context.add_note(f"Requirement '{requirement.name}' planned artefacts complete")


def ensure_lab_values(context: GenerationContext, patient: Patient, links: dict) -> None:
    from endoreg_db.models import LabValue

    lab_values: List[LabValue] = links.get("lab_values", [])
    if not lab_values:
        context.add_note("  no lab values referenced; skipping")
        return

    sample = None
    for lab_value in lab_values:
        existing = PatientLabValue.objects.filter(patient=patient, lab_value=lab_value).first()
        if existing:
            context.lab_values.append(existing)
            context.add_note(f"  reused patient lab value '{lab_value.name}' (id={existing.pk})")
            continue

        if sample is None:
            sample = patient.create_lab_sample()
            context.add_note(f"  created lab sample id={sample.pk} for lab value generation")

        value = lab_value.get_normal_value(patient=patient)
        if value is None:
            age = patient.age()
            if age is None:
                age = 40  # naive fallback for missing DOB
            normal_range = lab_value.get_normal_range(age=age, gender=patient.gender)
            value = normal_range.get("min") or normal_range.get("max") or 0.0

        patient_lab_value = PatientLabValue.objects.create(
            patient=patient,
            lab_value=lab_value,
            value=value if isinstance(value, (int, float)) else None,
            value_str=None if isinstance(value, (int, float)) else str(value),
            unit=lab_value.default_unit,
            sample=sample,
        )
        patient_lab_value.set_norm_values_from_default()
        context.lab_values.append(patient_lab_value)
        context.add_note(f"  created patient lab value '{lab_value.name}' -> {patient_lab_value.get_value()}")


def ensure_medications(context: GenerationContext, patient: Patient, links: dict) -> None:
    medications = links.get("medications", [])
    indications = links.get("medication_indications", [])
    intake_times = links.get("medication_intake_times", [])

    if not medications:
        context.add_note("  no medications referenced; skipping")
        return

    indication = indications[0] if indications else None

    for medication in medications:
        patient_medication, created = PatientMedication.objects.get_or_create(
            patient=patient,
            medication=medication,
            defaults={"medication_indication": indication},
        )

        if not created and indication and patient_medication.medication_indication is None:
            patient_medication.medication_indication = indication
            patient_medication.save(update_fields=["medication_indication"])

        if intake_times:
            patient_medication.intake_times.add(*intake_times)

        if created:
            context.medications.append(patient_medication)
            context.add_note(f"  created patient medication '{medication.name}' (id={patient_medication.pk})")
        else:
            context.add_note(f"  ensured patient medication '{medication.name}' exists (id={patient_medication.pk})")


def ensure_events(context: GenerationContext, patient: Patient, links: dict) -> None:
    events = links.get("events", [])
    if not events:
        context.add_note("  no events referenced; skipping")
        return

    today = timezone.now().date()
    for event in events:
        patient_event, created = PatientEvent.objects.get_or_create(
            patient=patient,
            event=event,
            defaults={"date_start": today},
        )
        if created:
            context.events.append(patient_event)
            context.add_note(f"  created patient event '{event.name}' on {patient_event.date_start}")
        else:
            context.add_note(f"  reused patient event '{event.name}' (id={patient_event.pk})")


def ensure_examinations_and_findings(context: GenerationContext, links: dict) -> None:
    examinations = links.get("examinations", [])
    findings = links.get("findings", [])

    target_exam_model = examinations[0] if examinations else None
    patient_exam = context.ensure_examination(target_exam_model)

    if not findings:
        return

    classification_choices = links.get("finding_classification_choices", [])
    interventions = links.get("finding_interventions", [])

    for finding in findings:
        patient_finding = patient_exam.patient_findings.filter(finding=finding).first()
        if not patient_finding:
            patient_finding = patient_exam.create_finding(finding)
            context.findings.append(patient_finding)
            context.add_note(f"  created patient finding '{finding.name}' in exam id={patient_exam.pk}")
        else:
            context.add_note(f"  reused patient finding '{finding.name}' (id={patient_finding.pk})")

        for choice in classification_choices:
            classification = choice.classifications.first()
            if classification is None:
                continue
            patient_finding.add_classification(
                classification_id=classification.id,
                classification_choice_id=choice.id,
            )
            context.add_note(f"    attached classification choice '{choice.name}'")

        for intervention in interventions:
            patient_finding.add_intervention(intervention_id=intervention.id, state="completed")
            context.add_note(f"    attached intervention '{intervention.name}'")


def execute_generation_plan(plan: RequirementSetPlan) -> GenerationContext:
    """Traverse the plan and emit high-level actions into the context."""

    context = initialize_generation_context()

    def _apply(current_plan: RequirementSetPlan) -> None:
        context.add_note(f"Evaluating RequirementSet '{current_plan.requirement_set.name}'")
        for req_plan in current_plan.requirement_plans:
            satisfy_requirement(req_plan, context)
        for child in current_plan.linked_set_plans:
            _apply(child)

    _apply(plan)
    return context


if __name__ == "__main__":
    # Pick a high-signal requirement set to stress the planner
    TARGET_REQ_SET = "high_bleed_risk_endoscopy_w_patient_w_anticoagulation"

    plan = generate_case_plan(TARGET_REQ_SET)
    pretty_print_plan(plan)

    ic("Total requirements discovered", len(list(plan.all_requirements())))

    context = execute_generation_plan(plan)
    ic("Generation context notes", context.notes)

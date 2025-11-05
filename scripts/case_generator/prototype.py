"""Quick-and-dirty sandbox for a RequirementSet driven case generator.

This module will evolve into a structured generator that can ingest a
``RequirementSet`` and materialise a patient record (examinations, lab values,
medications, events) satisfying all requirements. For now we only explore the
data structures and formulate an execution plan skeleton.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Iterable, List, Optional

import django
from icecream import ic


# Ensure Django is ready before importing models
DJANGO_SETTINGS_MODULE = os.environ.get("DJANGO_SETTINGS_MODULE")
django.setup()

from endoreg_db.models import Requirement, RequirementSet  # noqa: E402


@dataclass
class RequirementPlan:
    """Represents the actions required to satisfy a single Requirement."""

    requirement: Requirement
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

    return RequirementPlan(
        requirement=requirement,
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
        ic(
            prefix
            + "  Requirement: "
            + req.name
            + f" | operators={list(req.operators.values_list('name', flat=True))}"
        )
        if requirement_plan.inferred_models:
            ic(prefix + "    models => " + ", ".join(requirement_plan.inferred_models))
        for note in requirement_plan.notes:
            ic(prefix + "    note: " + note)
    for linked_plan in plan.linked_set_plans:
        pretty_print_plan(linked_plan, indent + 2)


if __name__ == "__main__":
    # Pick a high-signal requirement set to stress the planner
    TARGET_REQ_SET = "high_bleed_risk_endoscopy_w_patient_w_anticoagulation"

    plan = generate_case_plan(TARGET_REQ_SET)
    pretty_print_plan(plan)

    ic("Total requirements discovered", len(list(plan.all_requirements())))

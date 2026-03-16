from __future__ import annotations

from typing import Any, cast

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from endoreg_db.models import (
    Examination,
    LabValue,
    PatientExamination,
    PatientExaminationIndication,
    PatientFinding,
    PatientFindingClassification,
    PatientFindingIntervention,
    PatientLabValue,
)
from endoreg_db.services.lookup_service import (
    _finding_prefetch_bundle,
    _indication_prefetch_bundle,
    _lab_value_prefetch_bundle,
    _requirement_set_prefetch_bundle,
    load_patient_exam_for_eval,
)
from tests.helpers.default_objects import generate_patient


def test_load_patient_exam_prefetch_bundles_cover_expected_paths() -> None:
    indication_bundle = _indication_prefetch_bundle()
    lab_bundle = _lab_value_prefetch_bundle()
    finding_bundle = _finding_prefetch_bundle()
    requirement_bundle = _requirement_set_prefetch_bundle()

    assert [prefetch.prefetch_through for prefetch in indication_bundle] == [
        "indications"
    ]
    assert [prefetch.prefetch_through for prefetch in lab_bundle] == [
        "patient__lab_values"
    ]
    assert [prefetch.prefetch_through for prefetch in finding_bundle] == [
        "patient_findings"
    ]
    assert [prefetch.prefetch_through for prefetch in requirement_bundle] == [
        "examination__exam_reqset_links",
        "examination__exam_reqset_links__requirement_set",
    ]


def test_finding_prefetch_bundle_includes_active_subtrees() -> None:
    finding_bundle = _finding_prefetch_bundle()

    assert len(finding_bundle) == 1
    nested_prefetches = cast(Any, finding_bundle[0].queryset)._prefetch_related_lookups
    nested_paths = [prefetch.prefetch_through for prefetch in nested_prefetches]

    assert nested_paths == ["classifications", "interventions"]


def test_requirement_set_prefetch_bundle_includes_requirement_graph() -> None:
    requirement_bundle = _requirement_set_prefetch_bundle()

    assert len(requirement_bundle) == 2
    requirement_set_prefetch = requirement_bundle[1]

    assert requirement_set_prefetch.prefetch_through == (
        "examination__exam_reqset_links__requirement_set"
    )
    assert cast(Any, requirement_set_prefetch.queryset)._prefetch_related_lookups == (
        "requirements",
        "links_to_sets",
        "links_to_sets__requirements",
        "links_to_sets__requirement_set_type",
    )


@pytest.mark.django_db
def test_load_patient_exam_for_eval_prefetches_links_graph(base_db_data) -> None:
    patient = generate_patient()
    patient.save()

    examination = (
        Examination.objects.filter(
            findings__finding_classifications__choices__isnull=False,
            findings__finding_interventions__isnull=False,
            indications__isnull=False,
        )
        .distinct()
        .first()
    )
    assert examination is not None

    finding = (
        examination.findings.filter(
            finding_classifications__choices__isnull=False,
            finding_interventions__isnull=False,
        )
        .distinct()
        .first()
    )
    assert finding is not None

    classification = finding.finding_classifications.filter(
        choices__isnull=False
    ).first()
    assert classification is not None
    choice = classification.choices.first()
    assert choice is not None

    intervention = finding.finding_interventions.first()
    assert intervention is not None

    examination_indication = examination.indications.first()
    assert examination_indication is not None
    lab_value = LabValue.objects.first()
    assert lab_value is not None

    patient_examination = PatientExamination.objects.create(
        patient=patient,
        examination=examination,
    )
    PatientExaminationIndication.objects.create(
        patient_examination=patient_examination,
        examination_indication=examination_indication,
    )
    patient_finding = PatientFinding.objects.create(
        patient_examination=patient_examination,
        finding=finding,
    )
    PatientFindingClassification.objects.create(
        finding=patient_finding,
        classification=classification,
        classification_choice=choice,
        is_active=True,
    )
    PatientFindingIntervention.objects.create(
        finding=patient_finding,
        intervention=intervention,
        is_active=True,
    )
    PatientLabValue.objects.create(
        patient=patient,
        lab_value=lab_value,
        value=1.2,
    )

    loaded = load_patient_exam_for_eval(patient_examination.pk)

    with CaptureQueriesContext(connection) as ctx:
        links = loaded.links
        active_classifications = list(
            loaded.patient_findings.all()[0].active_classifications
        )
        active_interventions = list(
            loaded.patient_findings.all()[0].active_interventions
        )

    assert len(ctx) == 0, [query["sql"] for query in ctx.captured_queries]
    assert len(links.patient_findings) == 1
    assert len(links.findings) == 1
    assert len(links.finding_classifications) == 1
    assert len(links.finding_interventions) == 1
    assert len(links.patient_lab_values) == 1
    assert len(links.examination_indications) == 1
    assert len(active_classifications) == 1
    assert len(active_interventions) == 1

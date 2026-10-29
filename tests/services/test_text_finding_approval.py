from __future__ import annotations

from unittest.mock import Mock
from uuid import uuid4

import pytest
import spacy
from django.contrib.auth.models import User
from lx_dtypes.models.contracts.finding_extraction import (
    ClassificationTextRule,
    FindingMention,
)
from rest_framework.exceptions import ValidationError

from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.medical.finding.finding import Finding
from endoreg_db.models.medical.finding.finding_classification import (
    FindingClassification,
    FindingClassificationChoice,
)
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.medical.patient.patient_finding import PatientFinding
from endoreg_db.services.reports import text_finding_approval
from endoreg_db.services.reports.finding_extraction import (
    extract_classification_mentions,
)


def _language() -> spacy.language.Language:
    language = spacy.blank("de")
    language.add_pipe("sentencizer")
    return language


def test_classification_attaches_only_within_present_finding_sentence() -> None:
    text = "Polyp gestielt entfernt. Divertikel gestielt."
    mentions = [
        FindingMention(finding="polyp", presence="present", start=0, end=5),
        FindingMention(finding="diverticle", presence="absent", start=25, end=35),
    ]
    rule = ClassificationTextRule(
        classification="paris", choice="ip", pattern=[{"LOWER": "gestielt"}]
    )
    result = extract_classification_mentions(
        text,
        {"polyp": [rule], "diverticle": [rule]},
        mentions,
        nlp=_language(),
    )
    assert [(item.finding, item.start, item.end) for item in result] == [
        ("polyp", 6, 14)
    ]


def test_classification_without_present_mention_is_dropped() -> None:
    rule = ClassificationTextRule(
        classification="paris", choice="ip", pattern=[{"LOWER": "gestielt"}]
    )
    assert (
        extract_classification_mentions(
            "Kein Polyp, nichts gestielt.",
            {"polyp": [rule]},
            [FindingMention(finding="polyp", presence="absent", start=0, end=10)],
            nlp=_language(),
        )
        == []
    )


def _linked_report() -> tuple[RawPdfFile, PatientExamination, Finding]:
    patient = Patient.objects.create(
        patient_hash="text-approval-patient", first_name="Text", last_name="Approval"
    )
    examination = PatientExamination.objects.create(patient=patient)
    finding = Finding.objects.create(name="text_approval_polyp")
    classification = FindingClassification.objects.create(name="text_approval_paris")
    choice = FindingClassificationChoice.objects.create(
        name="text_approval_ip",
        description="Pedunculated",
        subcategories={},
        numerical_descriptors={},
    )
    classification.choices.add(choice)
    finding.finding_classifications.add(classification)
    report = RawPdfFile.objects.create(
        pdf_hash="c" * 64, anonymized_text="Polyp gestielt.", examination=examination
    )
    return report, examination, finding


@pytest.mark.django_db
def test_approval_records_provenance_classifications_and_refreshes_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report, examination, _finding = _linked_report()
    refresh = Mock()
    monkeypatch.setattr(
        text_finding_approval,
        "persist_patient_examination_dtypes_record_from_ledger",
        refresh,
    )
    user = User.objects.create(username="text-approver")
    instance = uuid4()
    kwargs = {
        "report": report,
        "patient_examination": examination,
        "finding_name": "text_approval_polyp",
        "instance_id": instance,
        "classifications": [("text_approval_paris", "text_approval_ip")],
        "user": user,
    }
    first = text_finding_approval.approve_text_finding(**kwargs)
    second = text_finding_approval.approve_text_finding(**kwargs)

    assert first.pk == second.pk
    assert PatientFinding.objects.count() == 1
    stored = PatientFinding.objects.get(instance_id=instance)
    assert stored.source == PatientFinding.Source.REPORT_TEXT
    assert stored.source_report_id == report.pk
    assert list(
        stored.classifications.filter(is_active=True).values_list(
            "classification__name", "classification_choice__name"
        )
    ) == [("text_approval_paris", "text_approval_ip")]
    assert refresh.call_count == 2


@pytest.mark.django_db
def test_approval_rejects_foreign_examination_and_manual_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report, examination, finding = _linked_report()
    monkeypatch.setattr(
        text_finding_approval,
        "persist_patient_examination_dtypes_record_from_ledger",
        Mock(),
    )
    user = User.objects.create(username="text-approver-2")
    other = PatientExamination.objects.create(patient=examination.patient)
    with pytest.raises(ValidationError, match="not linked"):
        text_finding_approval.approve_text_finding(
            report=report,
            patient_examination=other,
            finding_name=finding.name,
            instance_id=uuid4(),
            classifications=[],
            user=user,
        )
    manual = PatientFinding.objects.create(
        patient_examination=examination, finding=finding
    )
    with pytest.raises(ValidationError, match="another provenance"):
        text_finding_approval.approve_text_finding(
            report=report,
            patient_examination=examination,
            finding_name=finding.name,
            instance_id=manual.instance_id,
            classifications=[],
            user=user,
        )
    manual.refresh_from_db()
    assert manual.source == PatientFinding.Source.MANUAL

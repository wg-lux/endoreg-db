from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

import pytest
import spacy
from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import override_settings
from pydantic import ValidationError
from spacy.language import Language
from endoreg_db.import_files.report_import_service import ReportImportService
from endoreg_db.models.administration.center.center import Center
from endoreg_db.services.hub import ingest
from endoreg_db.services.hub.payloads import PreanonymizedIngestPayload
from endoreg_db.utils.paths import get_runtime_paths

# These tests exercise the publication boundary used by SAP watcher ingestion.
# pyright: reportPrivateUsage=false

from lx_dtypes.models.contracts.finding_extraction import FindingTextRule
from lx_dtypes.models.interface.KnowledgeBase import KnowledgeBase
from lx_dtypes.terminology.terminology_loader import get_terminology_service
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.services.reports import finding_extraction as extraction
from endoreg_db.utils.file_operations import atomic_write_file, ensure_directory
from endoreg_db.services.reports.finding_review import finding_suggestions
from endoreg_db.models.medical.finding.finding import Finding
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.media.frame.frame import Frame
from endoreg_db.models.label.label import Label
from endoreg_db.models.label.label_set import LabelSet
from endoreg_db.models.metadata.model_meta import ModelMeta
from endoreg_db.models.administration.ai.ai_model import AiModel
from endoreg_db.models.label.annotation.image_classification import (
    ImageClassificationAnnotation,
)
from lx_dtypes.terminology.terminology_loader import resolve_module_path
from django.contrib.auth.models import Group, User
from endoreg_db.helpers.typing import m2m_add_relation
from rest_framework.test import APIRequestFactory, force_authenticate
from endoreg_db.views.media.finding_suggestions import FindingSuggestionsView


@pytest.fixture
def language() -> Language:
    return spacy.blank("de")


@pytest.fixture
def package(tmp_path: Path) -> Iterator[KnowledgeBase]:
    root = ensure_directory(tmp_path / "registry")
    module = ensure_directory(tmp_path / "extraction_test")
    for name, text in {
        "config.yaml": "name: extraction_test\nversion: 1.0.0\ndata:\n  files: [findings.yml]\n",
        "findings.yml": (
            Path(__file__).parents[2] / "docs/examples/finding_extraction.yml"
        ).read_text(),
    }.items():
        atomic_write_file(destination=module / name, content=(text.encode(),))
    with override_settings(TERMINOLOGY_ROOT=root):
        get_terminology_service.cache_clear()
        service = get_terminology_service()
        service.register_local("extraction_test", "1.0.0", input_dirs=[module])
        service.select(
            "extraction_test",
            "1.0.0",
            expected_revision=service.list_bundles().revision,
            on_selected=lambda _: None,
            clear_application_caches=lambda: None,
        )
        try:
            yield service.load("extraction_test", "1.0.0")
        finally:
            get_terminology_service.cache_clear()


@pytest.mark.parametrize(
    ("text", "presence"),
    [
        ("Polyp", "present"),
        ("Keine Polypen", "absent"),
        ("Verdacht auf Polyp", "uncertain"),
        ("Unauffällig", None),
    ],
)
def test_yaml_rules_preserve_assertions(
    package: KnowledgeBase, language: Language, text: str, presence: str | None
) -> None:
    rules = {name: finding.text_extraction for name, finding in package.finding.items()}
    matches = extraction.extract_finding_mentions(text, rules, nlp=language)
    assert [(match.finding, match.presence) for match in matches] == (
        [("colon_polyp", presence)] if presence else []
    )
    if matches:
        assert text[matches[0].start : matches[0].end] == text
    assert extraction.extract_finding_mentions("Unauffällig", rules, nlp=language) == []


def test_invalid_pattern_fails(language: Language) -> None:
    rule = FindingTextRule(presence="present", pattern=[{"INVALID_ATTRIBUTE": "x"}])
    with pytest.raises(ValueError):
        extraction.extract_finding_mentions(
            "Polyp", {"colon_polyp": [rule]}, nlp=language
        )
    with pytest.raises(ValidationError):
        FindingTextRule.model_validate({"presence": "maybe", "pattern": []})


def test_concurrent_documents_do_not_share_mentions(
    package: KnowledgeBase, language: Language
) -> None:
    rules = {name: finding.text_extraction for name, finding in package.finding.items()}
    extract = partial(extraction.extract_finding_mentions, findings=rules, nlp=language)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(extract, ["Polyp", "Keine Polypen", "Unauffällig"] * 4))
    assert [[match.presence for match in matches] for matches in results] == [
        ["present"],
        ["absent"],
        [],
    ] * 4


@pytest.mark.django_db
def test_report_evidence_is_validated_and_idempotent(
    package: KnowledgeBase, language: Language, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert package.finding
    monkeypatch.setattr(
        extraction,
        "extract_finding_mentions",
        partial(extraction.extract_finding_mentions, nlp=language),
    )
    report = RawPdfFile.objects.create(
        pdf_hash="a" * 64, anonymized_text="Keine Polypen"
    )
    extraction.record_report_finding_presence(report)
    first = report.raw_meta
    extraction.record_report_finding_presence(report)
    report.refresh_from_db()
    assert report.raw_meta == first
    assert report.raw_meta is not None
    assert report.raw_meta["finding_extraction"] == {
        "knowledge_base_module": "extraction_test",
        "knowledge_base_version": "1.0.0",
        "source": "anonymized_text",
        "mentions": [
            {"finding": "colon_polyp", "presence": "absent", "start": 0, "end": 13}
        ],
    }
    report.raw_meta = {"finding_extraction": {"mentions": []}}
    with pytest.raises(DjangoValidationError):
        report.save(update_fields=["raw_meta"])


@pytest.mark.parametrize("text", ["Polyp", "Keine Polypen", "Verdacht auf Polyp"])
def test_fhir_uses_same_yaml_extractor(
    package: KnowledgeBase, language: Language, text: str
) -> None:
    result = package.import_terminology_validated_fhir_observations(
        [
            {
                "resourceType": "Observation",
                "code": {
                    "coding": [{"system": "urn:source", "code": "narrative"}],
                    "text": text,
                },
            }
        ],
        text_extractor=partial(extraction.extract_finding_mentions, nlp=language),
    )
    assert result["ok"]
    assert len(result["reported_findings"]) == (1 if text == "Polyp" else 0)
    assert result.get("extraction_mentions")


def test_fhir_canonical_code_wins_over_localized_display(
    package: KnowledgeBase,
) -> None:
    result = package.import_terminology_validated_fhir_observations(
        [
            {
                "resourceType": "Observation",
                "code": {
                    "coding": [
                        {
                            "system": "https://wg-lux.de/fhir/CodeSystem/lx-finding-cs",
                            "code": "colon-polyp",
                            "display": "Kolonpolyp",
                        }
                    ]
                },
            }
        ]
    )
    assert result["ok"]
    assert result["reported_findings"][0]["finding"] == "colon_polyp"


@pytest.mark.django_db
def test_sap_report_publication_records_configured_presence(
    package: KnowledgeBase, language: Language, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert package.finding
    monkeypatch.setattr(
        extraction,
        "extract_finding_mentions",
        partial(extraction.extract_finding_mentions, nlp=language),
    )
    source = (
        ensure_directory(get_runtime_paths().import_report) / "finding-presence.pdf"
    )
    atomic_write_file(
        destination=source,
        content=(ReportImportService._render_single_page_pdf("Polyp"),),
    )
    report = ingest._finalize_preanonymized_report(
        source_path=source,
        center=Center.objects.create(name="finding-extraction-center"),
        payload=PreanonymizedIngestPayload(anonymized_text="Polyp"),
        delete_source=False,
    )
    report.refresh_from_db()
    assert report.raw_meta is not None
    assert report.raw_meta["finding_extraction"] == {
        "knowledge_base_module": "extraction_test",
        "knowledge_base_version": "1.0.0",
        "source": "anonymized_text",
        "mentions": [
            {"finding": "colon_polyp", "presence": "present", "start": 0, "end": 5}
        ],
    }


def test_fhir_explicit_absence_is_not_a_positive_finding(
    package: KnowledgeBase,
) -> None:
    result = package.import_terminology_validated_fhir_observations(
        [
            {
                "resourceType": "Observation",
                "code": {
                    "coding": [
                        {
                            "system": "https://wg-lux.de/fhir/CodeSystem/lx-finding-cs",
                            "code": "colon-polyp",
                        }
                    ]
                },
                "valueBoolean": False,
            }
        ]
    )
    assert result["ok"]
    assert result["reported_findings"] == []


@pytest.mark.django_db
def test_review_preserves_report_assertions_and_stable_identity(
    package: KnowledgeBase, language: Language, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert package.finding
    Finding.objects.create(name="colon_polyp")
    monkeypatch.setattr(
        extraction,
        "extract_finding_mentions",
        partial(extraction.extract_finding_mentions, nlp=language),
    )
    report = RawPdfFile.objects.create(
        pdf_hash="b" * 64, anonymized_text="Keine Polypen"
    )
    extraction.record_report_finding_presence(report)
    first = finding_suggestions(report)
    assert first.examination is None
    assert first.candidates[0].presence == "absent"
    assert first.candidates[0].evidence == ["Keine Polypen"]
    assert first == finding_suggestions(report)


@pytest.mark.django_db
def test_video_review_requires_model_predictions_and_matching_mapping(
    package: KnowledgeBase,
) -> None:
    assert package.finding
    Finding.objects.create(name="colon_polyp")
    video = VideoFile.objects.create(
        raw_video_hash="review-video",
        center=Center.objects.create(name="review-video-center"),
    )
    assert finding_suggestions(video).candidates == []
    frame = Frame.objects.create(video=video, frame_number=1)
    label = Label.objects.create(name="polyp")
    annotation = ImageClassificationAnnotation.objects.create(
        frame=frame, label=label, value=True
    )
    assert finding_suggestions(video).candidates == []
    labelset = LabelSet.objects.create(name="review-labels", version=1)
    meta = ModelMeta.objects.create(
        name="review-model",
        version="1",
        model=AiModel.objects.create(name="review-model"),
        labelset=labelset,
    )
    annotation.model_meta = meta
    annotation.save()
    assert finding_suggestions(video).candidates == []
    mapping_file = (
        resolve_module_path("extraction_test", version="1.0.0")
        / "annotation_mappings.yml"
    )
    mapping = """schema_version: coloreg_annotation_mapping_v1
knowledge_base_module: extraction_test
knowledge_base_version: 1.0.0
labelset_names: [review-labels]
mappings:
  - label: polyp
    modalities: [frame]
    finding_name: colon_polyp
"""
    atomic_write_file(destination=mapping_file, content=(mapping.encode(),))
    candidates = finding_suggestions(video).candidates
    assert len(candidates) == 1
    assert candidates[0].name == "colon_polyp"
    assert candidates[0].source == "prediction"
    atomic_write_file(
        destination=mapping_file,
        content=(mapping.replace("version: 1.0.0", "version: 2.0.0").encode(),),
    )
    with pytest.raises(ValueError, match="selected terminology version"):
        finding_suggestions(video)
    atomic_write_file(
        destination=mapping_file,
        content=(mapping.replace("[review-labels]", "[other-labels]").encode(),),
    )
    assert finding_suggestions(video).candidates == []
    atomic_write_file(destination=mapping_file, content=(mapping.encode(),))
    annotation.value = False
    annotation.save()
    assert finding_suggestions(video).candidates == []


@pytest.mark.django_db
def test_suggestion_api_requires_authentication_and_center_scope() -> None:
    center = Center.objects.create(name="private-review-center")
    report = RawPdfFile.objects.create(pdf_hash="c" * 64, center=center)
    view = FindingSuggestionsView.as_view()
    request = APIRequestFactory().get("/")
    assert view(request, kind="pdfs", pk=report.pk).status_code in {401, 403}
    user = User.objects.create_user(username="review-reader")
    m2m_add_relation(getattr(user, "groups")).add(
        Group.objects.create(name="patient:read")
    )
    request = APIRequestFactory().get("/")
    force_authenticate(request, user=user)
    assert view(request, kind="pdfs", pk=report.pk).status_code == 404
    admin = User.objects.create_superuser(username="review-admin")
    request = APIRequestFactory().get("/")
    force_authenticate(request, user=admin)
    response = view(request, kind="pdfs", pk=report.pk)
    assert response.status_code == 200
    assert response["Cache-Control"] == "private, no-store"
    report.raw_meta = {
        "finding_extraction": {
            "knowledge_base_module": "extraction_test",
            "knowledge_base_version": "1.0.0",
            "mentions": [
                {"finding": "colon_polyp", "presence": "present", "start": 0, "end": 5}
            ],
        }
    }
    report.save(update_fields=["raw_meta"])
    assert view(request, kind="pdfs", pk=report.pk).status_code == 409

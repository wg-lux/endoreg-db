from __future__ import annotations

from endoreg_db.services.tabular_import_formats import (
    build_preanonymized_payload,
    load_document_templates,
    normalize_document_row,
    resolve_document_template,
)


def test_load_document_templates_includes_configured_formats() -> None:
    templates = load_document_templates()

    assert any(template.document_type == "cwd" for template in templates)
    assert any(template.document_type == "radiologie" for template in templates)
    assert any(template.document_type == "meona_medikamente" for template in templates)


def test_resolve_document_template_matches_cwd_headers() -> None:
    match = resolve_document_template(
        (
            "PatientNr",
            "FallNr",
            "Dokumentzeit",
            "Dokumentnummer",
            "Dokumentversion",
            "pmdAnam",
        )
    )

    assert match.template.document_type == "cwd"
    assert match.unknown_columns == ()


def test_normalize_document_row_builds_preanonymized_payload_for_cwd() -> None:
    normalized = normalize_document_row(
        {
            "PatientNr": "2000007988",
            "FallNr": "0016361635",
            "Dokumentzeit": "2024-10-14 10:10:00.000",
            "Dokumentnummer": "0000000000000010076929212",
            "Dokumentversion": "3",
            "pmdAnam": "already anonymized summary",
        }
    )

    payload = build_preanonymized_payload(
        normalized,
        source_system="cwd_export",
        center_name="test-center",
    )

    assert normalized["document_type"] == "cwd"
    assert normalized["canonical_row"]["patient_nr"] == "2000007988"
    assert payload["external_id"] == "2000007988"
    assert payload["casenumber"] == "0016361635"
    assert payload["anonymized_text"] == "already anonymized summary"
    assert payload["examination_date"] == "2024-10-14"
    assert payload["examination_time"] == "10:10:00"
    assert payload["center_name"] == "test-center"
    assert payload["source_document_type"] == "cwd"


def test_normalize_document_row_preserves_unknown_columns() -> None:
    normalized = normalize_document_row(
        {
            "PatientNr": "2000007988",
            "PatientAlter": "65",
            "Geschlecht": "female",
            "ExtraColumn": "surplus",
        }
    )

    assert normalized["document_type"] == "patienten"
    assert normalized["unknown_columns"] == {"ExtraColumn": "surplus"}

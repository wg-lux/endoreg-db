from __future__ import annotations

from importlib import import_module

import pytest


migration = import_module(
    "endoreg_db.migrations.0072_patient_examination_report_provenance"
)


@pytest.mark.parametrize(
    ("editor_payload", "expected_language", "expected_payload"),
    [
        ({}, "de", {"report_language": "de"}),
        (
            {"report_language": "en", "sections": []},
            "en",
            {"report_language": "en", "sections": []},
        ),
        (
            {"reportLanguage": "en", "sections": []},
            "en",
            {"report_language": "en", "sections": []},
        ),
        (
            {"report_language": "de", "reportLanguage": "de"},
            "de",
            {"report_language": "de"},
        ),
    ],
)
def test_legacy_report_language_backfill_is_canonical(
    editor_payload: object,
    expected_language: str,
    expected_payload: dict[str, object],
) -> None:
    language, canonical_payload = migration.legacy_report_language(
        editor_payload,
        report_id=17,
    )

    assert language == expected_language
    assert canonical_payload == expected_payload


@pytest.mark.parametrize(
    "editor_payload",
    [
        [],
        {"report_language": "fr"},
        {"report_language": "de", "reportLanguage": "en"},
    ],
)
def test_legacy_report_language_backfill_fails_closed(editor_payload: object) -> None:
    with pytest.raises(ValueError, match="Report 17"):
        migration.legacy_report_language(editor_payload, report_id=17)

"""Arrange–Act–Assert tests for patient identity grouping without database access."""

# Exercise the pure grouping boundary without mocking its database-backed caller.
# pyright: reportPrivateUsage=false

from datetime import date

import pytest

from endoreg_db.models import Examination, Patient, PatientExamination
from endoreg_db.services.study_cohort import _build_case_rows, _PreviewMedia


@pytest.mark.parametrize("same_patient_record", [True, False])
def test_same_hash_groups_follow_up_examinations_into_one_case(
    same_patient_record: bool,
) -> None:
    # Arrange
    patient = Patient(pk=1, patient_hash="shared-identity", is_real_person=False)
    follow_up_patient = (
        patient
        if same_patient_record
        else Patient(pk=2, patient_hash="shared-identity", is_real_person=False)
    )
    examination = Examination(pk=1, name="colonoscopy")
    initial = PatientExamination(
        pk=10,
        patient=patient,
        examination=examination,
        hash="initial-examination",
        date_start=date(2026, 1, 1),
    )
    follow_up = PatientExamination(
        pk=20,
        patient=follow_up_patient,
        examination=examination,
        hash="follow-up-examination",
        date_start=date(2026, 9, 1),
    )
    media = _PreviewMedia(
        reports_by_case={
            10: [
                {
                    "id": 100,
                    "patient_examination_id": 10,
                    "stream_url": "/reports/100/",
                    "availability": "available",
                    "document_type": "endoscopy",
                }
            ]
        },
        videos_by_case={
            20: [
                {
                    "id": 200,
                    "patient_examination_id": 20,
                    "stream_url": "/videos/200/",
                    "availability": "available",
                }
            ]
        },
        center_options={},
        center_keys_by_case={},
        document_types=set(),
        video_ids=[200],
    )

    # Act
    rows = _build_case_rows(
        [follow_up, initial],
        media=media,
        findings_by_case={},
        annotations_by_video={},
    )

    # Assert
    assert len(rows) == 1
    row = rows[0]
    assert row["patient_hash"] == "shared-identity"
    assert row["patient_examination_ids"] == [20, 10]
    assert row["case_hashes"] == ["follow-up-examination", "initial-examination"]
    assert [item["examination_date"] for item in row["examinations"]] == [
        "2026-09-01",
        "2026-01-01",
    ]
    assert row["reports"] == media.reports_by_case[10]
    assert row["videos"] == media.videos_by_case[20]


def test_different_hashes_keep_cases_separate_despite_matching_demographics() -> None:
    # Arrange
    examination = Examination(pk=1, name="colonoscopy")
    patients = [
        Patient(
            pk=index,
            first_name="Same",
            last_name="Pseudonym",
            dob=date(1980, 1, 1),
            patient_hash=identity,
            is_real_person=False,
        )
        for index, identity in enumerate(("identity-a", "identity-b"), start=1)
    ]
    examinations = [
        PatientExamination(
            pk=index,
            patient=patient,
            examination=examination,
            hash=f"examination-{index}",
            date_start=date(2026, 1, 1),
        )
        for index, patient in enumerate(patients, start=1)
    ]
    media = _PreviewMedia(
        reports_by_case={},
        videos_by_case={
            index: [
                {
                    "id": index,
                    "patient_examination_id": index,
                    "stream_url": f"/videos/{index}/",
                    "availability": "available",
                }
            ]
            for index in (1, 2)
        },
        center_options={},
        center_keys_by_case={},
        document_types=set(),
        video_ids=[1, 2],
    )

    # Act
    rows = _build_case_rows(
        examinations,
        media=media,
        findings_by_case={},
        annotations_by_video={},
    )

    # Assert
    assert len(rows) == 2
    assert [row["patient_hash"] for row in rows] == ["identity-a", "identity-b"]
    assert [row["patient_examination_ids"] for row in rows] == [[1], [2]]
    assert [[video["id"] for video in row["videos"]] for row in rows] == [[1], [2]]


@pytest.mark.parametrize("patient_hash", [None, "", " ", "\t\n"])
def test_missing_hash_rejects_grouping(patient_hash: str | None) -> None:
    # Arrange
    examination = PatientExamination(
        pk=1,
        patient=Patient(pk=1, patient_hash=patient_hash),
        examination=Examination(pk=1, name="colonoscopy"),
    )
    media = _PreviewMedia(
        reports_by_case={},
        videos_by_case={},
        center_options={},
        center_keys_by_case={},
        document_types=set(),
        video_ids=[],
    )

    # Act
    with pytest.raises(ValueError) as error:
        _build_case_rows(
            [examination],
            media=media,
            findings_by_case={},
            annotations_by_video={},
        )

    # Assert
    assert str(error.value) == "Study cohort examination must have a patient hash."

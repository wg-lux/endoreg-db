from __future__ import annotations

from typing import Any, Protocol, cast
from uuid import uuid4

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from endoreg_db.models import (
    Case,
    Center,
    Examination,
    Patient,
    PatientExamination,
    PatientExaminationReport,
    RawPdfFile,
    VideoFile,
)
from endoreg_db.serializers.video_examination import VideoExaminationSerializer


def _pk(instance: object) -> int:
    return int(cast(Any, instance).pk)


class _SerializerDataLike(Protocol):
    @property
    def data(self) -> object: ...


@pytest.mark.django_db
def test_case_examination_keeps_multiple_text_pdf_and_video_documents(
    api_client: APIClient,
) -> None:
    center = Center.objects.create(name=f"case-media-{uuid4().hex[:8]}")
    patient = Patient.objects.create(
        center=center,
        patient_hash=f"case-media-patient-{uuid4().hex}",
        first_name="Case",
        last_name="Media",
    )
    examination_type = Examination.objects.create(
        name=f"case-media-examination-{uuid4().hex}"
    )
    patient_examination = PatientExamination.objects.create(
        patient=patient,
        examination=examination_type,
        hash=f"case-media-pe-{uuid4().hex}",
    )
    patient_case = Case.objects.create(
        patient=patient,
        start_date=timezone.now(),
    )
    patient_case.patient_examinations.add(patient_examination)

    pdfs = [
        RawPdfFile.objects.create(
            center=center,
            patient=patient,
            examination=patient_examination,
            pdf_hash=f"case-media-pdf-{uuid4().hex}",
            file=f"sensitive/report-{uuid4().hex}.pdf",
            anonymized_text=f"Repeated report {index}",
        )
        for index in range(2)
    ]
    videos: list[VideoFile] = []
    for index in range(2):
        video = VideoFile.objects.create(
            center=center,
            patient=patient,
            video_hash=f"case-media-video-{uuid4().hex}",
            original_file_name=f"recording-{index}.mp4",
        )
        attach_response = api_client.post(
            f"/api/cases/{patient_case.case_id}/documents/",
            data={
                "media_type": "video",
                "media_id": _pk(video),
                "patient_examination_id": _pk(patient_examination),
            },
            format="json",
        )
        assert attach_response.status_code == 200, attach_response.content
        videos.append(video)

    reports = [
        PatientExaminationReport.objects.create(
            patient_examination=patient_examination,
            template_name="repeatable-report",
            title=f"Text report {index}",
            rendered_text=f"Text occurrence {index}",
        )
        for index in range(2)
    ]

    response = api_client.get(f"/api/cases/{patient_case.case_id}/")

    assert response.status_code == 200, response.content
    documents = cast(list[dict[str, object]], response.data["documents"])
    assert len(documents) == 6
    assert {(document["media_type"], document["id"]) for document in documents} == {
        *(("pdf", _pk(pdf)) for pdf in pdfs),
        *(("video", _pk(video)) for video in videos),
        *(("text_report", _pk(report)) for report in reports),
    }
    assert {document["patient_examination_id"] for document in documents} == {
        _pk(patient_examination)
    }
    assert list(
        patient_examination.video_files.order_by("id").values_list("id", flat=True)
    ) == [_pk(video) for video in videos]
    examination_payload = cast(
        dict[str, object],
        cast(
            _SerializerDataLike,
            VideoExaminationSerializer(patient_examination),
        ).data,
    )
    assert examination_payload["video_ids"] == [_pk(video) for video in videos]
    assert examination_payload["video_id"] is None


@pytest.mark.django_db
def test_case_document_attachment_is_idempotent_and_strict(
    api_client: APIClient,
) -> None:
    center = Center.objects.create(name=f"case-attach-{uuid4().hex[:8]}")
    patient = Patient.objects.create(
        center=center,
        patient_hash=f"case-attach-patient-{uuid4().hex}",
        first_name="Attach",
        last_name="Patient",
    )
    patient_examination = PatientExamination.objects.create(
        patient=patient,
        hash=f"case-attach-pe-{uuid4().hex}",
    )
    patient_case = Case.objects.create(patient=patient, start_date=timezone.now())
    patient_case.patient_examinations.add(patient_examination)
    pdf = RawPdfFile.objects.create(
        center=center,
        patient=patient,
        pdf_hash=f"case-attach-pdf-{uuid4().hex}",
        file=f"sensitive/report-{uuid4().hex}.pdf",
    )
    endpoint = f"/api/cases/{patient_case.case_id}/documents/"
    payload = {
        "media_type": "pdf",
        "media_id": _pk(pdf),
        "patient_examination_id": _pk(patient_examination),
    }

    first_response = api_client.post(endpoint, data=payload, format="json")
    second_response = api_client.post(endpoint, data=payload, format="json")

    assert first_response.status_code == 200, first_response.content
    assert first_response.resolver_match.url_name == "case-attach-document"
    assert second_response.status_code == 200, second_response.content
    assert [
        document
        for document in second_response.data["documents"]
        if document["media_type"] == "pdf" and document["id"] == _pk(pdf)
    ] == [
        {
            "media_type": "pdf",
            "id": _pk(pdf),
            "uuid": str(pdf.uuid),
            "patient_examination_id": _pk(patient_examination),
            "occurrence_at": second_response.data["documents"][0]["occurrence_at"],
            "file_name": pdf.file.name,
        }
    ]
    pdf.refresh_from_db()
    assert pdf.examination_id == _pk(patient_examination)

    invalid_response = api_client.post(
        endpoint,
        data={**payload, "unexpected": "value"},
        format="json",
    )
    bool_id_response = api_client.post(
        endpoint,
        data={**payload, "media_id": True},
        format="json",
    )

    assert invalid_response.status_code == 422
    assert invalid_response.data["code"] == "validation-error"
    assert bool_id_response.status_code == 422


@pytest.mark.django_db
def test_case_document_attachment_rejects_foreign_and_conflicting_resources(
    api_client: APIClient,
) -> None:
    center = Center.objects.create(name=f"case-conflict-{uuid4().hex[:8]}")
    patient = Patient.objects.create(
        center=center,
        patient_hash=f"case-conflict-owner-{uuid4().hex}",
        first_name="Case",
        last_name="Owner",
    )
    other_patient = Patient.objects.create(
        center=center,
        patient_hash=f"case-conflict-foreign-{uuid4().hex}",
        first_name="Foreign",
        last_name="Patient",
    )
    patient_examination = PatientExamination.objects.create(
        patient=patient,
        hash=f"case-conflict-pe-{uuid4().hex}",
    )
    outside_examination = PatientExamination.objects.create(
        patient=patient,
        hash=f"case-conflict-outside-{uuid4().hex}",
    )
    foreign_examination = PatientExamination.objects.create(
        patient=other_patient,
        hash=f"case-conflict-foreign-pe-{uuid4().hex}",
    )
    patient_case = Case.objects.create(patient=patient, start_date=timezone.now())
    patient_case.patient_examinations.add(patient_examination)
    endpoint = f"/api/cases/{patient_case.case_id}/documents/"

    unlinked_video = VideoFile.objects.create(
        center=center,
        patient=patient,
        video_hash=f"case-conflict-unlinked-{uuid4().hex}",
    )
    outside_response = api_client.post(
        endpoint,
        data={
            "media_type": "video",
            "media_id": _pk(unlinked_video),
            "patient_examination_id": _pk(outside_examination),
        },
        format="json",
    )
    assert outside_response.status_code == 404
    unlinked_video.refresh_from_db()
    assert unlinked_video.examination_id is None

    foreign_video = VideoFile.objects.create(
        center=center,
        patient=other_patient,
        examination=foreign_examination,
        video_hash=f"case-conflict-foreign-video-{uuid4().hex}",
    )
    foreign_response = api_client.post(
        endpoint,
        data={
            "media_type": "video",
            "media_id": _pk(foreign_video),
            "patient_examination_id": _pk(patient_examination),
        },
        format="json",
    )
    assert foreign_response.status_code == 404
    foreign_video.refresh_from_db()
    assert foreign_video.examination_id == _pk(foreign_examination)

    conflicting_video = VideoFile.objects.create(
        center=center,
        patient=patient,
        examination=outside_examination,
        video_hash=f"case-conflict-linked-video-{uuid4().hex}",
    )
    conflict_response = api_client.post(
        endpoint,
        data={
            "media_type": "video",
            "media_id": _pk(conflicting_video),
            "patient_examination_id": _pk(patient_examination),
        },
        format="json",
    )
    assert conflict_response.status_code == 409
    conflicting_video.refresh_from_db()
    assert conflicting_video.examination_id == _pk(outside_examination)

    patient_case.is_active = False
    patient_case.is_closed = True
    patient_case.save(update_fields=["is_active", "is_closed"])
    closed_video = VideoFile.objects.create(
        center=center,
        patient=patient,
        video_hash=f"case-conflict-closed-video-{uuid4().hex}",
    )
    closed_response = api_client.post(
        endpoint,
        data={
            "media_type": "video",
            "media_id": _pk(closed_video),
            "patient_examination_id": _pk(patient_examination),
        },
        format="json",
    )
    assert closed_response.status_code == 409
    closed_video.refresh_from_db()
    assert closed_video.examination_id is None

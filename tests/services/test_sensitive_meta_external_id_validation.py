from __future__ import annotations

from typing import Literal

import pytest
from lx_dtypes.models.contracts.pdf_file import PdfFileMetaJsonObject
from lx_dtypes.models.contracts.video_text_metadata import VideoTextMetaPayload

from endoreg_db.models.administration.person.patient.patient_external_id import (
    PatientExternalID,
)
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.services.raw_pdf_files import validate_report_metadata_annotation
from endoreg_db.services.video_files import validate_video_metadata_annotation
from tests.helpers.default_objects import get_default_center


pytestmark = pytest.mark.django_db


def _sensitive_meta() -> SensitiveMeta:
    return SensitiveMeta.create_from_dict(
        {
            "center": get_default_center(),
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "1994-03-21",
            "patient_gender": "male",
            "examination_date": "2024-02-15",
            "casenumber": "CASE-18",
        }
    )


def _validation_payload(
    *, external_id: str, external_id_origin: str
) -> PdfFileMetaJsonObject:
    return {
        "patient_first_name": "Max",
        "patient_last_name": "Mustermann",
        "patient_dob": "1994-03-21",
        "patient_gender": "male",
        "examination_date": "2024-02-15",
        "casenumber": "CASE-18",
        "external_id": external_id,
        "external_id_origin": external_id_origin,
    }


def _validate_media(
    *,
    media_type: Literal["video", "report"],
    sensitive_meta: SensitiveMeta,
    payload: PdfFileMetaJsonObject,
) -> bool:
    center = get_default_center()
    if media_type == "video":
        video = VideoFile.objects.create(
            center=center,
            sensitive_meta=sensitive_meta,
        )
        return validate_video_metadata_annotation(
            video,
            VideoTextMetaPayload.model_validate(payload),
        )

    report = RawPdfFile.objects.create(
        center=center,
        sensitive_meta=sensitive_meta,
    )
    return validate_report_metadata_annotation(
        report,
        payload,
        delete_original_raw=False,
        enforce_processed_artifact=False,
    )


@pytest.mark.parametrize("media_type", ["video", "report"])
def test_validation_ignores_blank_external_id_pair(
    base_db_data: object,
    media_type: Literal["video", "report"],
) -> None:
    sensitive_meta = _sensitive_meta()

    validated = _validate_media(
        media_type=media_type,
        sensitive_meta=sensitive_meta,
        payload=_validation_payload(external_id="", external_id_origin=""),
    )

    assert validated is True
    sensitive_meta.refresh_from_db()
    assert sensitive_meta.external_id is None
    assert PatientExternalID.objects.count() == 0


@pytest.mark.parametrize("media_type", ["video", "report"])
def test_validation_resolves_or_creates_populated_external_id_pair(
    base_db_data: object,
    media_type: Literal["video", "report"],
) -> None:
    sensitive_meta = _sensitive_meta()
    payload = _validation_payload(
        external_id="  patient-18  ",
        external_id_origin="  hospital-a  ",
    )

    assert (
        _validate_media(
            media_type=media_type,
            sensitive_meta=sensitive_meta,
            payload=payload,
        )
        is True
    )

    sensitive_meta.refresh_from_db()
    patient_external_id = sensitive_meta.external_id
    assert patient_external_id is not None
    assert patient_external_id.external_id == "patient-18"
    assert patient_external_id.origin == "hospital-a"
    assert patient_external_id.patient == sensitive_meta.pseudo_patient
    assert (
        PatientExternalID.objects.filter(
            external_id="patient-18",
            origin="hospital-a",
        ).count()
        == 1
    )

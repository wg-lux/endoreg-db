from __future__ import annotations

# pyright: reportPrivateUsage=false

from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from django.contrib.auth.models import User
from django.test import Client
from lx_dtypes.models import SensitiveMeta as LxSensitiveMeta
from lx_dtypes.models.contracts.json_types import JsonObject
from pydantic import ValidationError

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.processing.report_processing.report_anonymization import (
    persist_report_anonymization_result,
)
from endoreg_db.import_files.processing.video_processing.video_anonymization import (
    VideoAnonymizer,
)
from endoreg_db.models import (
    Center,
    PortalUserInfo,
    RawPdfFile,
    SensitiveMeta,
    VideoFile,
)
from endoreg_db.utils.hashs import (
    get_identity_salt,
    get_patient_examination_hash,
    get_patient_hash,
)


@dataclass(frozen=True)
class ReportResult:
    original_text: str
    anonymized_text: str
    extracted_metadata: LxSensitiveMeta


def _persist_video(video: VideoFile, payload: JsonObject) -> None:
    assert video.center is not None
    context = ImportContext(
        file_path=Path("metadata-contract.mp4"),
        center_name=str(video.center.name),
        current_video=video,
    )
    # Metadata persistence has no dependency on model loading or media encoding.
    anonymizer = VideoAnonymizer.__new__(VideoAnonymizer)
    anonymizer._persist_anonymizer_metadata(context, payload)


def _identity(meta: SensitiveMeta) -> tuple[str, str, int, int]:
    assert meta.pseudo_patient_id is not None
    assert meta.pseudo_examination_id is not None
    return (
        str(meta.patient_hash),
        str(meta.examination_hash),
        int(meta.pseudo_patient_id),
        int(meta.pseudo_examination_id),
    )


@pytest.mark.django_db
@pytest.mark.parametrize("legacy_aliases", [False, True])
@pytest.mark.parametrize("german_dates", [False, True])
def test_extraction_resolves_same_identity_and_review_for_video_and_report(
    base_db_data: bool,
    client: Client,
    legacy_aliases: bool,
    german_dates: bool,
) -> None:
    center = Center.objects.create(name=f"metadata-contract-{uuid4().hex}")
    user = User.objects.create_user(username=f"metadata-review-{uuid4().hex}")
    PortalUserInfo.objects.create(user=user).centers.add(center)
    client.force_login(user)
    video = VideoFile.objects.create(center=center, raw_video_hash=uuid4().hex)
    report = RawPdfFile.objects.create(center=center, pdf_hash=uuid4().hex)
    canonical: JsonObject = {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "dob": "10.12.1980" if german_dates else "1980-12-10",
        "examination_date": "21.09.2026" if german_dates else "2026-09-21",
    }
    payload = dict(canonical)
    if legacy_aliases:
        for source, target in (
            ("first_name", "patient_first_name"),
            ("last_name", "patient_last_name"),
            ("dob", "patient_dob"),
        ):
            payload[target] = payload.pop(source)
    # Mixed FrameCleaner output may also carry non-metadata fields.
    payload["frame_observations"] = []

    _persist_video(video, payload)
    persist_report_anonymization_result(
        report_id=report.pk,
        result=ReportResult("", "", LxSensitiveMeta.model_validate(canonical)),
    )
    video.refresh_from_db()
    report.refresh_from_db()
    video_meta = video.sensitive_meta
    report_meta = report.sensitive_meta
    assert video_meta is not None
    assert report_meta is not None
    assert video_meta.patient_first_name == "Ada"
    assert video_meta.patient_last_name == "Lovelace"
    assert _identity(video_meta) == _identity(report_meta)

    # Preserve the deployed double-SHA256 identity convention exactly.
    expected_patient_hash = sha256(
        get_patient_hash(
            "Ada", "Lovelace", date(1980, 12, 10), center.name, get_identity_salt()
        ).encode()
    ).hexdigest()
    expected_examination_hash = sha256(
        get_patient_examination_hash(
            "Ada",
            "Lovelace",
            date(1980, 12, 10),
            center.name,
            date(2026, 9, 21),
            get_identity_salt(),
        ).encode()
    ).hexdigest()
    assert video_meta.patient_hash == expected_patient_hash
    assert video_meta.examination_hash == expected_examination_hash

    original_identity = _identity(video_meta)
    original_meta_id = video_meta.pk
    for repeated_payload in (payload, {}):
        _persist_video(video, repeated_payload)
        video.refresh_from_db()
        assert video.sensitive_meta is not None
        assert video.sensitive_meta.pk == original_meta_id
        assert _identity(video.sensitive_meta) == original_identity

    for media_type, media in (("videos", video), ("pdfs", report)):
        response = client.get(f"/api/media/{media_type}/{media.pk}/sensitive-metadata/")
        assert response.status_code == 200
        data = cast(dict[str, object], response.json())
        assert media.sensitive_meta is not None
        assert data["id"] == media.sensitive_meta.pk
        assert data["patient_first_name"] == "Ada"
        assert data["patient_last_name"] == "Lovelace"


@pytest.mark.django_db
def test_invalid_recognized_metadata_does_not_create_a_relation(
    base_db_data: bool,
) -> None:
    center = Center.objects.create(name=f"metadata-invalid-{uuid4().hex}")
    video = VideoFile.objects.create(center=center, raw_video_hash=uuid4().hex)
    count_before = SensitiveMeta.objects.count()

    with pytest.raises(ValidationError):
        _persist_video(video, {"patient_first_name": {"invalid": "object"}})

    video.refresh_from_db()
    assert video.sensitive_meta is None
    assert SensitiveMeta.objects.count() == count_before


@pytest.mark.django_db
@pytest.mark.parametrize("media_type", ["videos", "pdfs"])
def test_review_read_does_not_create_missing_sensitive_metadata(
    base_db_data: bool, client: Client, media_type: str
) -> None:
    center = Center.objects.create(name=f"metadata-missing-{uuid4().hex}")
    user = User.objects.create_user(username=f"missing-review-{uuid4().hex}")
    PortalUserInfo.objects.create(user=user).centers.add(center)
    client.force_login(user)
    media = (
        VideoFile.objects.create(center=center, raw_video_hash=uuid4().hex)
        if media_type == "videos"
        else RawPdfFile.objects.create(center=center, pdf_hash=uuid4().hex)
    )
    count_before = SensitiveMeta.objects.count()

    response = client.get(f"/api/media/{media_type}/{media.pk}/sensitive-metadata/")

    assert response.status_code == 404
    media.refresh_from_db()
    assert media.sensitive_meta is None
    assert SensitiveMeta.objects.count() == count_before


@pytest.mark.parametrize("dob", [date(1980, 12, 10), datetime(1980, 12, 10, 17, 30)])
def test_legacy_hash_encoding_is_stable(dob: date) -> None:
    # Pin the historical duplicated date-of-birth encoding; changing it needs migration.
    raw = "AdaLovelace1980-12-10contract-center1980-12-10contract-salt"
    assert (
        get_patient_hash("Ada", "Lovelace", dob, "contract-center", "contract-salt")
        == sha256(raw.encode()).hexdigest()
    )

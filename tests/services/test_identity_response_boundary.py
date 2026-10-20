# Pure response and filename boundaries are intentionally exercised directly.
# pyright: reportPrivateUsage=false
from __future__ import annotations

from collections.abc import Iterator
from typing import cast
from unittest.mock import patch

import pytest
from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse, StreamingHttpResponse

from endoreg_db.models import Patient, PatientExamination
from endoreg_db.serializers.patient.patient import PatientSerializer
from endoreg_db.serializers.patient.patient_dropdown import PatientDropdownSerializer
from endoreg_db.serializers.patient_examination.patient_examination import (
    PatientExaminationSerializer,
)
from endoreg_db.services.interoperability.fhir_r4 import (
    _patient_resource,
)
from endoreg_db.services.privacy.response_boundary import (
    HeaderValidatedCsvResponse,
    IdentityHashResponseMiddleware,
)
from endoreg_db.services.studies.revision_export import case_export_workbook

pytestmark = pytest.mark.no_db
HASH = "a" * 64


def guarded(
    response: HttpResponse | StreamingHttpResponse,
) -> HttpResponse | StreamingHttpResponse:
    return IdentityHashResponseMiddleware(lambda _request: response)(HttpRequest())


def assert_withheld(response: HttpResponse | StreamingHttpResponse) -> None:
    assert isinstance(response, JsonResponse)
    assert response.status_code == 409
    assert HASH.encode() not in response.content
    assert b"identity_hash_response_blocked" in response.content


@pytest.mark.parametrize(
    "field",
    [
        "patient_hash",
        "examination_hash",
        "identity_fingerprint",
        "hash",
        "patient_hash_display",
        "examination_hash_display",
        "patient_pseudonym",
        "case_hashes",
    ],
)
def test_nested_browser_identity_is_withheld(field: str) -> None:
    assert_withheld(guarded(JsonResponse({"results": [{field: HASH}]})))


def test_validation_error_cannot_echo_hash_through_input_field() -> None:
    assert_withheld(
        guarded(JsonResponse({"errors": [{"loc": ["patient_hash"], "input": HASH}]}))
    )


@pytest.mark.parametrize(
    "content_type", ["text/html", "text/csv", "text/plain", "application/fhir+xml"]
)
def test_non_json_identity_response_is_withheld(content_type: str) -> None:
    assert_withheld(
        guarded(HttpResponse(f"patient_hash: {HASH}", content_type=content_type))
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "content_type", ["application/json", "text/csv", "text/html", "text/event-stream"]
)
def test_deferred_metadata_never_emits_a_chunk(content_type: str) -> None:
    consumed: list[bool] = []

    def chunks() -> Iterator[bytes]:
        consumed.append(True)
        yield HASH.encode()

    assert_withheld(guarded(StreamingHttpResponse(chunks(), content_type=content_type)))
    assert not consumed


def test_file_checksums_and_video_streaming_are_preserved() -> None:
    response = JsonResponse({"resource_hash": HASH, "processed_file_sha256": HASH})
    assert guarded(response) is response
    stream = StreamingHttpResponse(iter([b"processed-media"]), content_type="video/mp4")
    assert guarded(stream) is stream
    assert list(cast(Iterator[bytes], stream.streaming_content)) == [b"processed-media"]


def test_hash_free_schema_names_are_metadata_not_identity_fields() -> None:
    response = JsonResponse(
        {
            "components": {"schemas": {"PatientPseudonymPayload": {"type": "object"}}},
            "$ref": "#/components/schemas/PatientPseudonymPayload",
        }
    )
    assert guarded(response) is response


def test_csv_contract_is_checked_before_streaming_rows() -> None:
    consumed: list[bool] = []

    def lines() -> Iterator[str]:
        yield "video_id,frame_index\r\n"
        consumed.append(True)
        yield "17,2\r\n"

    response = HeaderValidatedCsvResponse(lines())
    assert guarded(response) is response
    assert not consumed
    assert (
        b"".join(cast(Iterator[bytes], response.streaming_content))
        == b"video_id,frame_index\r\n17,2\r\n"
    )


@pytest.mark.parametrize("header", ["patient_hash", "examination_hash", "case_hashes"])
def test_csv_identity_columns_are_rejected_before_rows(header: str) -> None:
    consumed: list[bool] = []

    def lines() -> Iterator[str]:
        yield header + "\r\n"
        consumed.append(True)
        yield HASH + "\r\n"

    with pytest.raises(ValueError, match="must remain on the source"):
        HeaderValidatedCsvResponse(lines())
    assert not consumed


def test_validated_spreadsheet_response_is_allowed() -> None:
    response = HttpResponse(
        b"validated-workbook",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    assert guarded(response) is response


def test_sensitive_response_header_is_not_forwarded() -> None:
    response = HttpResponse("safe")
    response["X-Patient-Hash"] = HASH
    result = guarded(response)
    assert_withheld(result)
    assert "X-Patient-Hash" not in result


def test_actual_patient_and_examination_serializers_cannot_disclose_hashes() -> None:
    patient = Patient(
        pk=1,
        patient_hash=HASH,
        first_name="Synthetic",
        last_name="Person",
        is_real_person=False,
    )
    examination = PatientExamination(pk=2, patient=patient, hash=HASH)
    for payload in (
        PatientSerializer().to_representation(patient),
        PatientDropdownSerializer().to_representation(patient),
        PatientExaminationSerializer().to_representation(examination),
    ):
        response = guarded(JsonResponse(dict(payload)))
        assert response.status_code == 200
        assert HASH.encode() not in response.content
        assert b"hash" not in response.content


def test_browser_filenames_do_not_reuse_source_identity() -> None:
    from endoreg_db.models import VideoFile
    from endoreg_db.serializers.misc.file_overview import FileOverviewSerializer
    from endoreg_db.serializers.video.video_file_list import VideoFileListSerializer

    video = VideoFile(pk=17, original_file_name=f"{HASH}.mp4")
    assert VideoFileListSerializer().get_original_file_name(video) == "video-17"
    assert FileOverviewSerializer()._display_filename(video) == "video-17"


def test_timeline_patient_references_are_hash_free() -> None:
    from endoreg_db.views.media.patient_media_timeline import (
        _patient_payload,
        _patient_ref,
    )

    patient = Patient(
        pk=1, patient_hash=HASH, first_name="Synthetic", last_name="Person"
    )
    for payload in (_patient_payload(patient), _patient_ref(patient)):
        response = guarded(JsonResponse(payload))
        assert response.status_code == 200
        assert HASH.encode() not in response.content


def test_fhir_uses_local_id_and_legacy_workbooks_reject_before_publication() -> None:
    patient = Patient(pk=1, patient_hash=HASH, is_real_person=False)
    examination = PatientExamination(pk=2, patient=patient, hash=HASH)
    resource = _patient_resource(examination)
    assert HASH not in resource.model_dump_json()
    assert resource.identifier[0].value == "1"
    with patch("endoreg_db.services.studies.revision_export.Workbook") as workbook:
        with pytest.raises(ValueError, match="must remain on the source"):
            case_export_workbook({"patient_pseudonym": HASH})
        workbook.assert_not_called()


def test_application_installs_response_guard() -> None:
    middleware = cast(list[str], settings.MIDDLEWARE)
    assert (
        "endoreg_db.services.privacy.response_boundary.IdentityHashResponseMiddleware"
        in middleware
    )


@pytest.mark.parametrize("body", [b"not-json", b"[" * 2000 + b"0" + b"]" * 2000])
def test_uninspectable_json_is_withheld(body: bytes) -> None:
    assert_withheld(guarded(HttpResponse(body, content_type="application/json")))

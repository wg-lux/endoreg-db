"""Wire round trips through shared contracts and host compatibility boundaries."""

from copy import deepcopy

import pytest
from pydantic import ValidationError
from lx_dtypes.models.contracts import fhir_clinical as shared_fhir
from lx_dtypes.models.contracts.video_frame_annotations import (
    FrameBoxAnnotationBulkEnvelopePayload,
)

from endoreg_db.schemas.fhir_r4 import FhirCoding, FhirReference
from endoreg_db.schemas.frame_annotation_ingress import (
    validate_frame_box_annotation_bulk_ingress,
)

pytestmark = pytest.mark.no_db


def box() -> dict[str, object]:
    return dict(
        label_id=2, x=10, y=20, width=30, height=40, image_width=800, image_height=600
    )


@pytest.mark.parametrize("empty", [None, ""])
def test_legacy_empty_box_values_inherit_shared_defaults_without_mutating_input(
    empty: object,
) -> None:
    item = {
        **box(),
        "frame_id": empty,
        "annotator": empty,
        "information_source_name": empty,
    }
    payload = {
        "frame_id": 8,
        "annotator": " alice ",
        "information_source": " reviewed ",
        "annotations": [item],
    }
    original = deepcopy(payload)

    result = validate_frame_box_annotation_bulk_ingress(payload)
    roundtrip = FrameBoxAnnotationBulkEnvelopePayload.model_validate_json(
        result.model_dump_json(exclude_none=True)
    )

    assert result == roundtrip
    assert result.annotations[0].frame_id == 8
    assert result.annotations[0].annotator == "alice"
    assert result.annotations[0].information_source_name == "reviewed"
    assert payload == original


def test_explicit_null_wrapper_values_never_become_text_none() -> None:
    result = validate_frame_box_annotation_bulk_ingress(
        {
            "frame_id": 8,
            "annotator": None,
            "information_source_name": None,
            "information_source": None,
            "annotations": [box()],
        }
    )

    assert result.annotator is None
    assert result.resolved_information_source_name is None
    assert result.annotations[0].annotator is None
    assert result.annotations[0].information_source_name == "manual_annotation"


@pytest.mark.parametrize(
    "patch",
    [
        {"frame_id": 9},
        {"frame_id": []},
        {"information_source_name": False},
        {"unknown": 1},
    ],
)
def test_invalid_box_values_reach_shared_validation(patch: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        validate_frame_box_annotation_bulk_ingress(
            {"frame_id": 8, "annotations": [{**box(), **patch}]}
        )


def test_list_envelope_preserves_explicit_item_values() -> None:
    result = validate_frame_box_annotation_bulk_ingress(
        [
            {
                **box(),
                "frame_id": 8,
                "annotator": "bob",
                "information_source_name": "reviewed",
            }
        ]
    )
    assert result.annotations[0].annotator == "bob"
    assert result.annotations[0].information_source_name == "reviewed"


@pytest.mark.parametrize(
    ("host", "shared", "data"),
    [
        (
            FhirCoding,
            shared_fhir.FhirCoding,
            {"system": "urn:test", "code": "valid", "display": "Example"},
        ),
        (
            FhirReference,
            shared_fhir.FhirReference,
            {"reference": "Patient/example", "display": "Example"},
        ),
    ],
)
def test_fhir_primitives_share_fields_and_roundtrip_without_weakening_host_profile(
    host: type[FhirCoding] | type[FhirReference],
    shared: type[shared_fhir.FhirCoding] | type[shared_fhir.FhirReference],
    data: dict[str, str],
) -> None:
    result = host.model_validate(data)
    canonical = shared.model_validate_json(result.model_dump_json())

    assert isinstance(result, shared)
    assert (
        host.model_validate_json(canonical.model_dump_json()).model_dump()
        == result.model_dump()
    )
    with pytest.raises(ValidationError):
        host.model_validate({**data, "unknown": True})
    invalid = dict.fromkeys(data, " ")
    with pytest.raises(ValidationError):
        host.model_validate(invalid)

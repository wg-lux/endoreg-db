from __future__ import annotations

import pytest

from endoreg_db.schemas.upload import (
    upload_api_request_data_from_mapping,
    validate_upload_api_request_payload,
)


def test_upload_schema_excludes_non_json_multipart_file_before_validation() -> None:
    multipart_data: dict[str, object] = {
        "center_key": "  site-a  ",
        "file": object(),
    }

    assert upload_api_request_data_from_mapping(multipart_data) == {
        "center_key": "site-a"
    }
    payload = validate_upload_api_request_payload(multipart_data)
    assert payload.center_key == "site-a"
    assert payload.source_system == "api"


def test_upload_schema_rejects_unknown_transport_fields_in_sorted_order() -> None:
    with pytest.raises(
        ValueError,
        match=r"Unknown upload request field\(s\): alpha, zeta",
    ):
        validate_upload_api_request_payload(
            {
                "file": object(),
                "zeta": "unexpected",
                "alpha": "unexpected",
            }
        )

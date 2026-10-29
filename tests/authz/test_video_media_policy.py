from __future__ import annotations

import pytest

from endoreg_db.authz.policy import get_needed_role


def test_decoded_frame_stream_route_is_video_read_scoped():
    assert get_needed_role("video-frame-decoded-stream", "GET") == "video:read"
    assert get_needed_role("video-frame-decoded-stream", "POST") == "video:write"


def test_quarantine_routes_are_anonymization_scoped():
    assert get_needed_role("quarantine-item-list", "GET") == "anonymization:read"
    assert get_needed_role("quarantine-sync", "POST") == "anonymization:write"
    assert (
        get_needed_role("quarantine-approve-deletion", "POST") == "anonymization:write"
    )
    assert get_needed_role("quarantine-reap-approved", "POST") == "anonymization:write"


def test_study_cohort_preview_is_patient_read_scoped() -> None:
    assert get_needed_role("study-cohort-preview", "GET") == "patient:read"


@pytest.mark.parametrize(
    "route_name",
    (
        "video-hls-playlist-m3u8",
        "video-hls-playlist",
        "video-hls-key",
        "video-hls-segment",
    ),
)
def test_hls_routes_are_explicitly_video_read_scoped(route_name: str) -> None:
    assert get_needed_role(route_name, "GET") == "video:read"

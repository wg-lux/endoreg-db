from __future__ import annotations

from endoreg_db.authz.policy import get_needed_role


def test_decoded_frame_stream_route_is_video_read_scoped():
    assert get_needed_role("video-frame-decoded-stream", "GET") == "video:read"
    assert get_needed_role("video-frame-decoded-stream", "POST") == "video:write"

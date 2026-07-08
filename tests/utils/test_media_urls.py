from __future__ import annotations

from types import SimpleNamespace

from endoreg_db.utils.media_urls import (
    build_absolute_media_url,
    build_patient_timeline_path,
    build_pdf_stream_path,
    build_video_hls_playlist_path,
    build_video_frame_decoded_stream_path,
    build_video_frame_stream_path,
    build_video_stream_path,
)


def test_build_video_stream_path_returns_legacy_compat_stream_url() -> None:
    assert build_video_stream_path(7) == "/endoreg-api/media/videos/7/stream/"
    assert (
        build_video_stream_path(7, file_type="processed")
        == "/endoreg-api/media/videos/7/stream/?type=processed"
    )


def test_build_video_stream_path_rejects_removed_mp4_modes() -> None:
    import pytest

    with pytest.raises(ValueError, match="only available for processed video"):
        build_video_stream_path(7, file_type="raw")
    with pytest.raises(ValueError, match="do not support download mode"):
        build_video_stream_path(7, file_type="processed", download=True)


def test_build_video_hls_playlist_path_supports_processed_type() -> None:
    assert (
        build_video_hls_playlist_path(7, file_type="processed")
        == "/endoreg-api/media/videos/7/hls/playlist.m3u8?type=processed"
    )


def test_build_pdf_stream_path_supports_type_and_download() -> None:
    assert build_pdf_stream_path(11) == "/endoreg-api/media/pdfs/11/stream/"
    assert (
        build_pdf_stream_path(11, file_type="processed")
        == "/endoreg-api/media/pdfs/11/stream/?type=processed"
    )
    assert (
        build_pdf_stream_path(11, file_type="raw", download=True)
        == "/endoreg-api/media/pdfs/11/stream/?type=raw&download=1"
    )


def test_build_patient_timeline_path_supports_optional_filter() -> None:
    assert build_patient_timeline_path(5) == "/endoreg-api/media/patients/5/timeline/"
    assert (
        build_patient_timeline_path(5, patient_examination_id=9)
        == "/endoreg-api/media/patients/5/timeline/?patient_examination_id=9"
    )


def test_build_video_frame_stream_path_returns_canonical_frame_endpoint() -> None:
    assert (
        build_video_frame_stream_path(7, 42)
        == "/endoreg-api/media/videos/7/frames/42/stream/"
    )


def test_build_video_frame_decoded_stream_path_supports_file_type() -> None:
    assert (
        build_video_frame_decoded_stream_path(7, 42, file_type="processed")
        == "/endoreg-api/media/videos/7/frames/42/decoded-stream/?file_type=processed"
    )


def test_build_absolute_media_url_uses_request_when_available() -> None:
    request = SimpleNamespace(
        build_absolute_uri=lambda path: (  # pyright: ignore[reportUnknownLambdaType]
            f"https://hub.test{path}"
        )
    )
    assert (
        build_absolute_media_url(
            request,
            "/endoreg-api/media/pdfs/11/stream/?type=processed",
        )
        == "https://hub.test/endoreg-api/media/pdfs/11/stream/?type=processed"
    )
    assert (
        build_absolute_media_url(
            None,
            "/endoreg-api/media/pdfs/11/stream/?type=processed",
        )
        == "/endoreg-api/media/pdfs/11/stream/?type=processed"
    )

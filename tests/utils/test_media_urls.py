from __future__ import annotations

from types import SimpleNamespace

from endoreg_db.utils.media_urls import (
    build_absolute_media_url,
    build_patient_timeline_path,
    build_pdf_stream_path,
    build_video_frame_decoded_stream_path,
    build_video_frame_stream_path,
    build_video_stream_path,
)


def test_build_video_stream_path_supports_type_and_download() -> None:
    assert build_video_stream_path(7) == "/api/media/videos/7/stream/"
    assert (
        build_video_stream_path(7, file_type="processed")
        == "/api/media/videos/7/stream/?type=processed"
    )
    assert (
        build_video_stream_path(7, file_type="raw", download=True)
        == "/api/media/videos/7/stream/?type=raw&download=1"
    )


def test_build_pdf_stream_path_supports_type_and_download() -> None:
    assert build_pdf_stream_path(11) == "/api/media/pdfs/11/stream/"
    assert (
        build_pdf_stream_path(11, file_type="processed")
        == "/api/media/pdfs/11/stream/?type=processed"
    )
    assert (
        build_pdf_stream_path(11, file_type="raw", download=True)
        == "/api/media/pdfs/11/stream/?type=raw&download=1"
    )


def test_build_patient_timeline_path_supports_optional_filter() -> None:
    assert build_patient_timeline_path(5) == "/api/media/patients/5/timeline/"
    assert (
        build_patient_timeline_path(5, patient_examination_id=9)
        == "/api/media/patients/5/timeline/?patient_examination_id=9"
    )


def test_build_video_frame_stream_path_returns_canonical_frame_endpoint() -> None:
    assert (
        build_video_frame_stream_path(7, 42) == "/api/media/videos/7/frames/42/stream/"
    )


def test_build_video_frame_decoded_stream_path_supports_file_type() -> None:
    assert (
        build_video_frame_decoded_stream_path(7, 42, file_type="processed")
        == "/api/media/videos/7/frames/42/decoded-stream/?file_type=processed"
    )


def test_build_absolute_media_url_uses_request_when_available() -> None:
    request = SimpleNamespace(
        build_absolute_uri=lambda path:  # pyright: ignore[reportUnknownLambdaType]
        f"https://hub.test{path}"
    )
    assert (
        build_absolute_media_url(request, "/api/media/pdfs/11/stream/?type=processed")
        == "https://hub.test/api/media/pdfs/11/stream/?type=processed"
    )
    assert (
        build_absolute_media_url(None, "/api/media/pdfs/11/stream/?type=processed")
        == "/api/media/pdfs/11/stream/?type=processed"
    )

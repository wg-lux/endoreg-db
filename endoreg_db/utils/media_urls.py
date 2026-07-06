from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from endoreg_db.utils.api_urls import endoreg_api_path


def _append_query(path: str, query: Mapping[str, Any] | None = None) -> str:
    if not query:
        return path

    parts: list[str] = []
    for key, value in query.items():
        if value is None:
            continue
        parts.append(f"{key}={value}")

    if not parts:
        return path
    return f"{path}?{'&'.join(parts)}"


def build_video_stream_path(
    video_id: int,
    *,
    file_type: str | None = None,
    download: bool | None = None,
) -> str:
    return _append_query(
        endoreg_api_path(f"media/videos/{video_id}/stream/"),
        {
            "type": file_type,
            "download": 1 if download else None,
        },
    )


def build_pdf_stream_path(
    pdf_id: int,
    *,
    file_type: str | None = None,
    download: bool | None = None,
) -> str:
    return _append_query(
        endoreg_api_path(f"media/pdfs/{pdf_id}/stream/"),
        {
            "type": file_type,
            "download": 1 if download else None,
        },
    )


def build_patient_timeline_path(
    patient_id: int,
    *,
    patient_examination_id: int | None = None,
) -> str:
    return _append_query(
        endoreg_api_path(f"media/patients/{patient_id}/timeline/"),
        {
            "patient_examination_id": patient_examination_id,
        },
    )


def build_video_frame_stream_path(
    video_id: int,
    frame_number: int,
) -> str:
    return endoreg_api_path(f"media/videos/{video_id}/frames/{frame_number}/stream/")


def build_video_frame_decoded_stream_path(
    video_id: int,
    frame_number: int,
    *,
    file_type: str,
) -> str:
    return _append_query(
        endoreg_api_path(
            f"media/videos/{video_id}/frames/{frame_number}/decoded-stream/"
        ),
        {"file_type": file_type},
    )


def build_video_hls_playlist_path(
    video_id: int,
    *,
    file_type: str | None = None,
) -> str:
    return _append_query(
        endoreg_api_path(f"media/videos/{video_id}/hls/playlist/"),
        {"type": file_type},
    )


def build_video_hls_key_path(video_id: int, key_id: str) -> str:
    return endoreg_api_path(f"media/videos/{video_id}/hls/key/{key_id}/")


def build_video_hls_segment_base_path(video_id: int, key_id: str) -> str:
    return endoreg_api_path(f"media/videos/{video_id}/hls/segments/{key_id}/")


def build_absolute_media_url(
    request: Any | None,
    relative_path: str,
) -> str:
    if request is None:
        return relative_path
    return request.build_absolute_uri(relative_path)

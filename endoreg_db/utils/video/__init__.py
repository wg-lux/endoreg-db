from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = [
    "get_video_key",
    "identify_video_key",
    "get_video_key_regex_by_examination_alias",
    "get_stream_info",
    "assemble_video_from_frames",
    "transcode_video",
    "transcode_videofile_if_required",
    "ffmpeg_extract_frames",
]

if TYPE_CHECKING:
    from endoreg_db.utils.ffmpeg_wrapper import (
        assemble_video_from_frames as assemble_video_from_frames,
        get_stream_info as get_stream_info,
        transcode_video as transcode_video,
        transcode_videofile_if_required as transcode_videofile_if_required,
    )
    from endoreg_db.utils.ffmpeg_wrapper import extract_frames as ffmpeg_extract_frames
    from endoreg_db.utils.video_names import (
        get_video_key as get_video_key,
        get_video_key_regex_by_examination_alias as get_video_key_regex_by_examination_alias,
        identify_video_key as identify_video_key,
    )


def __getattr__(name: str) -> Any:
    if name in {
        "get_video_key",
        "identify_video_key",
        "get_video_key_regex_by_examination_alias",
    }:
        from endoreg_db.utils import video_names

        value = getattr(video_names, name)
        globals()[name] = value
        return value

    if name == "ffmpeg_extract_frames":
        from endoreg_db.utils.ffmpeg_wrapper import extract_frames

        globals()[name] = extract_frames
        return extract_frames

    if name in {
        "get_stream_info",
        "assemble_video_from_frames",
        "transcode_video",
        "transcode_videofile_if_required",
    }:
        from endoreg_db.utils import ffmpeg_wrapper

        value = getattr(ffmpeg_wrapper, name)
        globals()[name] = value
        return value

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

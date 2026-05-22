from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile


def run_video_pipe_1(video: "VideoFile", *args, **kwargs) -> bool:
    from ._pipeline_1 import _pipe_1

    return _pipe_1(video, *args, **kwargs)


def test_after_video_pipe_1(video: "VideoFile", *args, **kwargs) -> bool:
    from ._pipeline_1 import _test_after_pipe_1

    return _test_after_pipe_1(video, *args, **kwargs)


def run_video_pipe_2(video: "VideoFile") -> bool:
    from ._pipeline_2 import _pipe_2

    return _pipe_2(video)

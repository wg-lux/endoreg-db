from pathlib import Path

from ...utils.ffmpeg_wrapper import assemble_video as ffmpeg_assemble_video

def assemble_video_from_frames(frame_pattern: str, output_path: Path, fps: float, width: int, height: int, codec: str = "libx264", pix_fmt: str = "yuv420p"):
    """Assembles a video from frames."""
    return ffmpeg_assemble_video(frame_pattern, output_path, fps, width, height, codec, pix_fmt)

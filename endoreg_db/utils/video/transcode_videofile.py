import shutil
import subprocess
import os
from pathlib import Path
from icecream import ic


def get_transcoded_file_path(source_file_path: Path, suffix: str = "mp4"):
    """
    Method to get the transcoded file path.

    Args:
        source_file_path (Path): Source file path.
        suffix (str): Suffix of the transcoded file.

    Returns:
        transcoded_file_path (Path): Transcoded file path.
    """
    transcoded_file_name = f"{source_file_path.stem}_transcoded.{suffix}"
    transcoded_file_path = source_file_path.parent / transcoded_file_name
    return transcoded_file_path


def check_require_transcode(
    filepath: Path, transcoded_file_path: Path, target_suffix=".mp4"
):
    """
    Checks whether a video file requires transcoding.\
    We check if the current suffix of the file path matches the target suffix\
    and if the transcoded file path exists.\
    If the current suffix does not match the target suffix and the transcoded file path does not exist,\
    transcoding is required.
    """
    current_suffix = filepath.suffix

    require_transcode = False
    if not current_suffix == target_suffix and not transcoded_file_path.exists():
        if not transcoded_file_path.exists():
            require_transcode = True
    return require_transcode


def transcode_videofile_if_required(filepath: Path):
    """
    Perform transcoding on a video file if required.
    This method checks whether a transcoded version (with an ".mp4" suffix) of the given
    video file exists or needs to be produced. It first computes the expected transcoded file path,
    then uses a class-specific check (check_require_transcode) to decide if transcoding is necessary.
    If so, it transcodes the video file by calling the class method transcode_videofile and returns
    the path of the transcoded file after ensuring that the resulting file path matches the computed one.
    If transcoding is not required, the original file path is returned.
    Args:
        filepath (Path): The path to the original video file that may require transcoding.
    Returns:
        Path: The path to the transcoded video file if transcoding was performed; otherwise, the original file path.
    """

    transcoded_file_path = get_transcoded_file_path(filepath, suffix=".mp4")
    if check_require_transcode(filepath, transcoded_file_path):
        transcoded_path = transcode_videofile(
            filepath, transcoded_path=transcoded_file_path
        )
        assert transcoded_file_path == transcoded_path
        return transcoded_path
    else:
        return filepath


def transcode_videofile(filepath: Path, transcoded_path: Path):
    """
    Transcodes a video to a compatible MP4 format using ffmpeg.
    If the transcoded file exists, it is returned.

    Parameters
    ----------
    mov_file : str
        The full path to the video file.

    Returns
    -------
    transcoded_path : str
        The full path to the transcoded video file.
    """
    ic("Transcoding video")
    ic(f"Input path: {filepath}")

    # if filepath suffix is .mp4 or .MP4 we dont need to transcode and can copy the file
    if filepath.suffix.lower() in [".mp4"]:
        shutil.copyfile(filepath, transcoded_path)
        return transcoded_path

    ic(f"Transcoded path: {transcoded_path}")
    if os.path.exists(transcoded_path):
        return transcoded_path

    # Run ffmpeg to transcode the video using H264 and AAC
    # TODO Document settings, check if we need to change them
    command = [
        "ffmpeg",
        "-i",
        filepath.resolve().as_posix(),
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-c:a",
        "aac",
        transcoded_path,
    ]
    subprocess.run(command, check=True)
    return transcoded_path

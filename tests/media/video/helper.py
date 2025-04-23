from pathlib import Path
import random
import re
from typing import Optional

from logging import getLogger
logger = getLogger(__name__)


from endoreg_db.utils.video.names import (
    identify_video_key,
    get_video_key,
    get_video_key_regex_by_examination_alias
)

from django.conf import settings

ASSET_DIR:Path = settings.ASSET_DIR
assert ASSET_DIR.exists(), f"ASSET_DIR does not exist: {ASSET_DIR}"

TEST_VIDEOS = {
    "egd-instrument-non_anonymous": ASSET_DIR / "test_instrument.mp4",
    "egd-endoscope-non_anonymous": ASSET_DIR / "test_endoscope.mp4",
    "egd-nbi-non_anonymous": ASSET_DIR / "test_nbi.mp4",
    "egd-outside-non_anonymous": ASSET_DIR / "test_outside.mp4",
    "egd-small_intestine-non_anonymous": ASSET_DIR / "test_small_intestine.mp4",
}

def get_video_path(video_key:str) -> Path:
    """
    Retrieves the video path based on the provided video key.
    """
    if video_key in TEST_VIDEOS:
        return TEST_VIDEOS[video_key]
    else:
        raise ValueError(f"Video key '{video_key}' not found in TEST_VIDEOS.")

def get_video_keys(
    examination_alias:Optional[str]=None, content:Optional[str]=None, is_anonymous:Optional[bool]=None
):
    """
    Retrieves video keys that match the provided regex pattern based on examination alias, content, and anonymity status.
    """
    pattern = get_video_key_regex_by_examination_alias(examination_alias, content, is_anonymous)
    logger.warning(pattern)
    return [key for key in TEST_VIDEOS.keys() if re.match(pattern, key)]

def get_random_video_path_by_examination_alias(
    examination_alias:Optional[str]=None, content:Optional[str]=None, is_anonymous:Optional[bool]=None
):
    """
    Retrieves a random video key that matches the provided regex pattern based on examination alias, content, and anonymity status.
    """
    keys = get_video_keys(examination_alias, content, is_anonymous)
    if keys:
        random_video_key = random.choice(keys)
        video_path = get_video_path(random_video_key)
        return video_path  # Return the first match for simplicity
    else:
        raise ValueError(f"No matching video keys found for the given criteria.")



TEST_VIDEOS = {key: value if value.exists() else None for key, value in TEST_VIDEOS.items()}

from pathlib import Path
import random
import re
from typing import Optional

from logging import getLogger
logger = getLogger(__name__)


from endoreg_db.utils.video.names import (
    get_video_key_regex_by_examination_alias
)

from django.conf import settings


ASSET_DIR:Path = settings.ASSET_DIR
assert ASSET_DIR.exists(), f"ASSET_DIR does not exist: {ASSET_DIR}"

_TEST_VIDEOS = {
    # "egd-instrument-non_anonymous": ASSET_DIR / "test_instrument.mp4", # No detected segments
    "egd-endoscope-non_anonymous": ASSET_DIR / "test_endoscope.mp4",
    "egd-nbi-non_anonymous": ASSET_DIR / "test_nbi.mp4",
    # "egd-outside-non_anonymous": ASSET_DIR / "test_outside.mp4",
    "egd-small_intestine-non_anonymous": ASSET_DIR / "test_small_intestine.mp4",
}

# Only keep entries that actually exist on disk to avoid returning None paths
TEST_VIDEOS = {key: value for key, value in _TEST_VIDEOS.items() if value.exists()}


def get_video_path(video_key:str) -> Path:
    """
    Returns the file path associated with the given video key.
    
    Raises:
        ValueError: If the video key does not exist in the available test videos.
    """
    if video_key in TEST_VIDEOS:
        return TEST_VIDEOS[video_key]
    else:
        raise ValueError(f"Video key '{video_key}' not found in TEST_VIDEOS.")

def get_video_keys(
    examination_alias:Optional[str]=None, content:Optional[str]=None, is_anonymous:Optional[bool]=None
):
    """
    Returns a list of video keys matching the specified examination alias, content type, and anonymity status.
    
    If no direct matches are found, falls back to suffix-based filtering for anonymity. Logs warnings and errors when fallback logic is used or no matches are found.
    
    Args:
        examination_alias: The examination alias to filter by, or None for any.
        content: The content type to filter by, or None for any.
        is_anonymous: Whether to filter for anonymous, non-anonymous, or both.
    
    Returns:
        A list of matching video keys.
    """
    # Build a more flexible pattern locally if content is None
    if content is None:
        pattern_parts = ["^"]
        if examination_alias:
            pattern_parts.append(re.escape(examination_alias))
            pattern_parts.append("-.*-") # Match any content
        else:
            pattern_parts.append(".*-") # Match any examination alias and content

        if is_anonymous is True:
            pattern_parts.append("anonymous$")
        elif is_anonymous is False:
            pattern_parts.append("non_anonymous$")
        else: # is_anonymous is None
            pattern_parts.append("(non_)?anonymous$") # Match either

        pattern = "".join(pattern_parts)
    else:
        # Use the imported function if content is specified
        pattern = get_video_key_regex_by_examination_alias(examination_alias, content, is_anonymous)
        logger.warning(f"Generated pattern (from imported function): {pattern}")


    # Only consider keys for which the file actually exists
    keys_to_check = list(TEST_VIDEOS.keys())
    matched_keys = [key for key in keys_to_check if re.match(pattern, key)]

    # Fallback logic remains as a safety net, but ideally shouldn't be needed now for this case
    if not matched_keys and is_anonymous is False:
        logger.warning(f"Pattern '{pattern}' yielded no results for is_anonymous=False. Falling back to suffix check '-non_anonymous'.")
        matched_keys = [key for key in keys_to_check if key.endswith("-non_anonymous")]
    elif not matched_keys and is_anonymous is True:
         logger.warning(f"Pattern '{pattern}' yielded no results for is_anonymous=True. Falling back to suffix check '-anonymous'.")
         matched_keys = [key for key in keys_to_check if key.endswith("-anonymous")]

    if not matched_keys:
        logger.error(f"No keys found matching pattern '{pattern}' or fallback logic for keys: {keys_to_check}")


    return matched_keys


def get_random_video_path_by_examination_alias(
    examination_alias:Optional[str]=None, content:Optional[str]=None, is_anonymous:Optional[bool]=None
):
    """
    Returns the file path of a randomly selected video matching the specified examination alias, content type, and anonymity status.
    
    Raises:
        ValueError: If no matching video keys are found for the given criteria.
    """
    keys = get_video_keys(examination_alias, content, is_anonymous)
    if keys:
        random_video_key = random.choice(keys)
        video_path = get_video_path(random_video_key)
        return video_path  # Return the first match for simplicity
    else:
        raise ValueError("No matching video keys found for the given criteria.")



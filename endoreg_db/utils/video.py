import logging
from pathlib import Path
from typing import Optional, List
import subprocess
import shutil # Keep for potential future use or remove if unused elsewhere

logger = logging.getLogger(__name__)

# transcode_videofile and transcode_videofile_if_required moved to utils.ffmpeg_wrapper
# ... (other potential video utilities can remain here) ...

__all__ = [
    # Add any remaining utility functions here
]

# If no other functions remain, this file could potentially be removed,
# but keep it for now in case other video utils are added later.

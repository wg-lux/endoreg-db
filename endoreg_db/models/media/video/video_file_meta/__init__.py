import logging

# Import functions from submodule files to make them available directly
from .text_meta import _update_text_metadata
from .video_meta import _update_video_meta
from .initialize_video_specs import _initialize_video_specs
from .get_fps import _get_fps
from .get_endo_roi import _get_endo_roi
from .get_crop_template import _get_crop_template


logger = logging.getLogger(__name__)

# Define __all__ if you want to control what `from .video_file_meta import *` imports
__all__ = [
    '_update_text_metadata',
    '_update_video_meta',
    '_initialize_video_specs',
    '_get_fps',
    '_get_endo_roi',
    '_get_crop_template',
]

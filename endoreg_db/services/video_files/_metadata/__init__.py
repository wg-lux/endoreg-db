# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportMissingTypeStubs=false
import logging

from .get_crop_template import _get_crop_template
from .get_endo_roi import get_endo_roi
from .get_fps import _get_fps
from .initialize_video_specs import _initialize_video_specs

# Import functions from submodule files to make them available directly
from .text_meta import _update_text_metadata
from .video_meta import (
    _get_import_context_names,
    _get_import_processor,
    _update_video_meta,
)

logger = logging.getLogger(__name__)

# Define __all__ if you want to control what `from .video_file_meta import *` imports
__all__ = [
    "_update_text_metadata",
    "_update_video_meta",
    "_get_import_processor",
    "_get_import_context_names",
    "_initialize_video_specs",
    "_get_fps",
    "get_endo_roi",
    "_get_crop_template",
]

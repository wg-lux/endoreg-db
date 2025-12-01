# File to set up data and a patient_examination factory for requirement set tests

# from ..helpers.data_loader import load_data_no_req
# from ..helpers.default_objects

import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from django.test import override_settings
import logging
from endoreg_db.models import Center
from ..helpers.default_objects import DEFAULT_CENTER_NAME
from endoreg_db.models import AiModel
from ..helpers.default_objects import DEFAULT_SEGMENTATION_MODEL_NAME
from endoreg_db.models import VideoFile
from endoreg_db.models.state.video import VideoState
from rest_framework.test import APIClient
from django.conf import settings
from django.conf import settings
from endoreg_db.models import ModelMeta
from endoreg_db.utils.video.ffmpeg_wrapper import get_stream_info
from django.db import connections

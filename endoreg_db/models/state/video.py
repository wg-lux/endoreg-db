from django.db import models
from typing import TYPE_CHECKING
from .abstract import AbstractState

class AbstractVideoState(AbstractState):
    """
    Abstract base class for video-related states.
    
    Tracks common processing state flags for video entities:
    - Whether frames have been extracted
    - Whether initial predictions have been generated
    - Whether label video segments have been created
    - Whether label video segments have been annotated
    """
    frames_extracted = models.BooleanField(default=False)
    initial_prediction = models.BooleanField(default=False)
    lvs_created = models.BooleanField(default=False)
    lvs_annotated = models.BooleanField(default=False)
    
    class Meta:
        abstract = True

class RawVideoFileState(AbstractVideoState):
    """State for raw video file data."""

    origin = models.OneToOneField(
        "RawVideoFile",
        on_delete=models.CASCADE,
        related_name="state",
        null=True,
        blank=True,
    )
    if TYPE_CHECKING:
        from endoreg_db.models import RawVideoFile

        origin: "RawVideoFile"

    class Meta:
        verbose_name = "Raw Video File State"
        verbose_name_plural = "Raw Video File States"

class VideoState(AbstractVideoState):
    """State for video data."""

    origin = models.OneToOneField(
        "Video",
        on_delete=models.CASCADE,
        related_name="state",
        null=True,
        blank=True,
    )
    if TYPE_CHECKING:
        from endoreg_db.models import Video
        origin: "Video"
    

    class Meta:
        verbose_name = "Video State"
        verbose_name_plural = "Video States"

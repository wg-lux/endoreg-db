from ..base_classes import AbstractVideo
from django.db import models

from endoreg_db.models.data_file.frame import Frame
from endoreg_db.models.data_file.frame import LegacyFrame

BATCH_SIZE = 1000

class Video(AbstractVideo):
    import_meta = models.OneToOneField('VideoImportMeta', on_delete=models.CASCADE, blank=True, null=True)
    def get_video_model(self):
        return Video

    def get_frame_model(self):
        return Frame


class LegacyVideo(AbstractVideo):
    file = models.FileField(upload_to="legacy_videos", blank=True, null=True)

    def get_video_model(self):
        return LegacyVideo

    def get_frame_model(self):
        return LegacyFrame

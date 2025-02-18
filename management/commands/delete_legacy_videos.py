# Django command to fetch all LegacyVideo objects, delete the associated file and delete the object from the database

from endoreg_db.models.data_file.video import LegacyVideo
from tqdm import tqdm
from django.core.management.base import BaseCommand

class Command(BaseCommand):
    """
    Deletes all LegacyVideos in the database.
    """
    help = 'Deletes all LegacyVideos in the database.'

    def handle(self, *args, **options):
        videos = LegacyVideo.objects.all()
        for video in tqdm(videos):
            video.file.delete()
            video.delete()
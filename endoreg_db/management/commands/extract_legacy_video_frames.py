# Django Command to:
# fetch all LegacyVideos and extract frames from them
# track progress for all videos and for each video with tqdm

from django.core.management.base import BaseCommand
from tqdm import tqdm

from endoreg_db.models.data_file.video import LegacyVideo


class Command(BaseCommand):
    """
    Extracts frames from all LegacyVideos in the database.
    """

    help = "Extracts frames from all LegacyVideos in the database."

    def handle(self, *args, **options):
        videos = LegacyVideo.objects.all()
        for video in tqdm(videos):
            video.extract_all_frames()

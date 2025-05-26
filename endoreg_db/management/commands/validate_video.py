from endoreg_db.models import VideoPredictionMeta
from typing import TYPE_CHECKING
from django.core.management.base import BaseCommand

"""
This command handles the video validation as seen in tests _pipe_1
"""

if TYPE_CHECKING:
    from endoreg_db.models import VideoFile, VideoState
    
class Command(BaseCommand):
    help = "Data extraction and validation of video files in the database and updating their states accordingly."
    def add_arguments(self, parser):
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Display verbose output",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Force revalidation of all video files, even if they are already validated.",
        )
        parser.add_argument(
            "--anonymize",
            action="store_true",
            help="Anonymize video files.",
        )

    def handle(self, *args, **options):
        """
        Validate video files in the database and update their states accordingly.
        """
        
    
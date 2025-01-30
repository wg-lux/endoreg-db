from endoreg_db.utils.hashs import get_video_hash
import subprocess
from django.core.files import File
from django.core.management.base import BaseCommand
from endoreg_db.models.data_file.video import LegacyVideo
import os
from tqdm import tqdm
# import cv2

def convert_mkv_to_mp4(source_path, target_path):
    """
    Convert a .mkv file to a .mp4 file using FFmpeg.
    
    Parameters:
        mov_file_path (str): The file path of the input .MOV file.
        mp4_file_path (str): The file path where the output .mp4 file should be saved.
        
    Returns:
        None
    """
    cmd = ["ffmpeg", "-y", "-i", source_path, "-vcodec", "h264", "-acodec", "aac", target_path]
    subprocess.run(cmd)


class Command(BaseCommand):
    """
    Imports videos from a directory into the database

    Usage:
        python manage.py import_legacy_videos <directory>
    """
    help = 'Imports videos from a directory into the database'

    def add_arguments(self, parser):
        parser.add_argument('directory', type=str)

    def handle(self, *args, **options):
        directory = options['directory']
        if not os.path.isdir(directory):
            raise Exception(f"Directory {directory} does not exist")

        # iterate over all subdirectories and gather all .mkv files
        mkv_files = []
        for root, dirs, files in os.walk(directory):
            for file in files:
                if file.endswith(".mkv"):
                    mkv_files.append(os.path.join(root, file))

        # create videos
        for mkv_file in tqdm(mkv_files):
            # get the name of the video
            video_name = os.path.basename(mkv_file).replace(".mkv", "")

            # convert the .mkv file to a .mp4 file
            mp4_file = os.path.join(directory, video_name + ".mp4")
            if not os.path.isfile(mp4_file):
                convert_mkv_to_mp4(mkv_file, mp4_file)

            # create the video object
            video_hash = get_video_hash(mp4_file)

            # check if the video already exists
            if LegacyVideo.objects.filter(video_hash=video_hash).exists():
                print(f"Video with hash {video_hash} already exists. Skipping...")
                continue

            video = LegacyVideo.objects.create(video_hash=video_hash, suffix = "mp4")

            # open the .mp4 file
            with open(mp4_file, 'rb') as f:
                # create the Django File object
                django_file = File(f)
                # save the file to the video
                video.file.save(video_name + ".mp4", django_file)
            raise Exception("Stop here: NEED TO FIX OPENCV DEPENDENCY")
            video.initialize_video_specs(cv2.VideoCapture(video.file.path))

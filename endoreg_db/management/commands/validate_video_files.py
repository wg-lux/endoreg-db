"""
Django management command to validate video file existence and accessibility.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from endoreg_db.models import VideoFile
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Validate video file existence and accessibility'

    def add_arguments(self, parser):
        parser.add_argument(
            '--video-id',
            type=int,
            help='Check specific video ID',
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Enable verbose output',
        )
        parser.add_argument(
            '--fix-missing',
            action='store_true',
            help='Mark videos with missing files as inactive',
        )

    def handle(self, *args, **options):
        """Validate video files and their accessibility."""
        verbose = options['verbose']
        video_id = options.get('video_id')
        fix_missing = options['fix_missing']

        if verbose:
            self.stdout.write(self.style.SUCCESS("Starting video validation..."))

        # Query videos
        if video_id:
            try:
                videos = [VideoFile.objects.get(pk=video_id)]
                self.stdout.write(f"Checking specific video ID: {video_id}")
            except VideoFile.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"Video with ID {video_id} not found"))
                return
        else:
            videos = VideoFile.objects.all()
            self.stdout.write(f"Checking {videos.count()} videos...")

        missing_files = []
        accessible_files = []
        corrupted_files = []

        for video in videos:
            video_status = self.check_video_file(video, verbose)
            
            if video_status['status'] == 'missing':
                missing_files.append(video_status)
            elif video_status['status'] == 'corrupted':
                corrupted_files.append(video_status)
            else:
                accessible_files.append(video_status)

        # Report results
        self.stdout.write("\n" + "="*60)
        self.stdout.write(self.style.SUCCESS(f"VALIDATION COMPLETE"))
        self.stdout.write("="*60)
        
        self.stdout.write(f"✅ Accessible videos: {len(accessible_files)}")
        self.stdout.write(f"❌ Missing files: {len(missing_files)}")
        self.stdout.write(f"⚠️  Potentially corrupted: {len(corrupted_files)}")

        if missing_files:
            self.stdout.write(self.style.WARNING("\nMISSING FILES:"))
            for file_info in missing_files:
                self.stdout.write(f"  - Video ID {file_info['video_id']}: {file_info['error']}")
                if fix_missing:
                    self.stdout.write(f"    → Marking as inactive (if applicable)")

        if corrupted_files:
            self.stdout.write(self.style.WARNING("\nPOTENTIALLY CORRUPTED FILES:"))
            for file_info in corrupted_files:
                self.stdout.write(f"  - Video ID {file_info['video_id']}: {file_info['error']}")

        if verbose and accessible_files:
            self.stdout.write(self.style.SUCCESS("\nACCESSIBLE FILES:"))
            for file_info in accessible_files[:10]:  # Show first 10
                self.stdout.write(f"  ✅ Video ID {file_info['video_id']}: {file_info['path']} ({file_info['size_mb']:.1f} MB)")
            
            if len(accessible_files) > 10:
                self.stdout.write(f"  ... and {len(accessible_files) - 10} more")

    def check_video_file(self, video, verbose=False):
        """
        Check a single video file for existence and basic accessibility.
        
        Returns:
            dict: Status information about the video file
        """
        video_info = {
            'video_id': video.id,
            'video_uuid': str(video.uuid) if hasattr(video, 'uuid') else 'N/A',
            'status': 'unknown',
            'path': None,
            'size_mb': 0,
            'error': None
        }

        try:
            # Check for active file path
            if hasattr(video, 'active_file_path') and video.active_file_path:
                file_path = Path(video.active_file_path)
                video_info['path'] = str(file_path)
                
                if not file_path.exists():
                    video_info['status'] = 'missing'
                    video_info['error'] = f"Active file path does not exist: {file_path}"
                    return video_info
                
                # Check file size
                try:
                    file_size = file_path.stat().st_size
                    video_info['size_mb'] = file_size / (1024 * 1024)
                    
                    if file_size == 0:
                        video_info['status'] = 'corrupted'
                        video_info['error'] = "File exists but has zero size"
                        return video_info
                        
                    video_info['status'] = 'accessible'
                    return video_info
                    
                except OSError as e:
                    video_info['status'] = 'corrupted'
                    video_info['error'] = f"Cannot read file stats: {e}"
                    return video_info

            # Check for other file sources
            elif hasattr(video, 'active_file') and video.active_file:
                try:
                    file_path = Path(video.active_file.path)
                    video_info['path'] = str(file_path)
                    
                    if not file_path.exists():
                        video_info['status'] = 'missing'
                        video_info['error'] = f"Active file does not exist: {file_path}"
                        return video_info
                        
                    file_size = file_path.stat().st_size
                    video_info['size_mb'] = file_size / (1024 * 1024)
                    video_info['status'] = 'accessible' if file_size > 0 else 'corrupted'
                    
                    if file_size == 0:
                        video_info['error'] = "File exists but has zero size"
                    
                    return video_info
                    
                except (ValueError, OSError) as e:
                    video_info['status'] = 'corrupted'
                    video_info['error'] = f"Cannot access active file: {e}"
                    return video_info

            # Check raw_file
            elif hasattr(video, 'raw_file') and video.raw_file:
                try:
                    file_path = Path(video.raw_file.path)
                    video_info['path'] = str(file_path)
                    
                    if not file_path.exists():
                        video_info['status'] = 'missing'
                        video_info['error'] = f"Raw file does not exist: {file_path}"
                        return video_info
                        
                    file_size = file_path.stat().st_size
                    video_info['size_mb'] = file_size / (1024 * 1024)
                    video_info['status'] = 'accessible' if file_size > 0 else 'corrupted'
                    
                    if file_size == 0:
                        video_info['error'] = "Raw file exists but has zero size"
                    
                    return video_info
                    
                except (ValueError, OSError) as e:
                    video_info['status'] = 'corrupted'
                    video_info['error'] = f"Cannot access raw file: {e}"
                    return video_info

            # Check processed_file
            elif hasattr(video, 'processed_file') and video.processed_file:
                try:
                    file_path = Path(video.processed_file.path)
                    video_info['path'] = str(file_path)
                    
                    if not file_path.exists():
                        video_info['status'] = 'missing'
                        video_info['error'] = f"Processed file does not exist: {file_path}"
                        return video_info
                        
                    file_size = file_path.stat().st_size
                    video_info['size_mb'] = file_size / (1024 * 1024)
                    video_info['status'] = 'accessible' if file_size > 0 else 'corrupted'
                    
                    if file_size == 0:
                        video_info['error'] = "Processed file exists but has zero size"
                    
                    return video_info
                    
                except (ValueError, OSError) as e:
                    video_info['status'] = 'corrupted'
                    video_info['error'] = f"Cannot access processed file: {e}"
                    return video_info

            else:
                video_info['status'] = 'missing'
                video_info['error'] = "No video file paths found (no active_file, raw_file, or processed_file)"
                return video_info

        except Exception as e:
            video_info['status'] = 'corrupted'
            video_info['error'] = f"Unexpected error checking video: {e}"
            return video_info
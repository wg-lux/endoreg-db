"""
Django management command to validate video file existence and accessibility.
"""
from django.core.management.base import BaseCommand

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
        self.stdout.write(self.style.SUCCESS("VALIDATION COMPLETE"))
        self.stdout.write("="*60)
        
        self.stdout.write(f"✅ Accessible videos: {len(accessible_files)}")
        self.stdout.write(f"❌ Missing files: {len(missing_files)}")
        self.stdout.write(f"⚠️  Potentially corrupted: {len(corrupted_files)}")

        if missing_files:
            self.stdout.write(self.style.WARNING("\nMISSING FILES:"))
            for file_info in missing_files:
                self.stdout.write(f"  - Video ID {file_info['video_id']}: {file_info['error']}")
                if fix_missing:
                    self.stdout.write("    → Marking as inactive (if applicable)")

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

        # Helper to check a file attribute
        def _check_file_attr(obj, attr, path_getter=None, label=None):
            if not hasattr(obj, attr):
                return None
            file_field = getattr(obj, attr)
            if not file_field:
                return None
            try:
                file_path = Path(path_getter(file_field) if path_getter else file_field)
                info = video_info.copy()
                info['path'] = str(file_path)
                if not file_path.exists():
                    info['status'] = 'missing'
                    info['error'] = f"{label or attr.replace('_', ' ').title()} does not exist: {file_path}"
                    return info
                file_size = file_path.stat().st_size
                info['size_mb'] = file_size / (1024 * 1024)
                if file_size == 0:
                    info['status'] = 'corrupted'
                    info['error'] = f"{label or attr.replace('_', ' ').title()} exists but has zero size"
                else:
                    info['status'] = 'accessible'
                return info
            except (ValueError, OSError) as e:
                info = video_info.copy()
                info['path'] = str(getattr(file_field, 'path', file_field))
                info['status'] = 'corrupted'
                info['error'] = f"Cannot access {label or attr.replace('_', ' ').title()}: {e}"
                return info

        # Try each file attribute in order of preference
        result = None
        # active_file_path: direct path string
        result = _check_file_attr(video, 'active_file_path', label='Active file path')
        if result: return result
        # active_file: Django FileField
        result = _check_file_attr(video, 'active_file', path_getter=lambda f: f.path, label='Active file')
        if result: return result
        # raw_file: Django FileField
        result = _check_file_attr(video, 'raw_file', path_getter=lambda f: f.path, label='Raw file')
        if result: return result
        # processed_file: Django FileField
        result = _check_file_attr(video, 'processed_file', path_getter=lambda f: f.path, label='Processed file')
        if result: return result

        # If none found
        video_info['status'] = 'missing'
        video_info['error'] = "No video file paths found (no active_file, raw_file, or processed_file)"
        return video_info
"""
Django management command to fix video file paths in the database.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from endoreg_db.models import VideoFile
from pathlib import Path
import logging
import os

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Fix video file paths in the database to match actual file locations'

    def add_arguments(self, parser):
        parser.add_argument(
            '--video-id',
            type=int,
            help='Fix specific video ID only',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be changed without making changes',
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Enable verbose output',
        )
        parser.add_argument(
            '--storage-dir',
            type=str,
            default=None,
            help='Path to the storage directory (default: $ENDOREG_STORAGE_DIR or ./storage)'
        )

    def handle(self, *args, **options):
        """
        Synchronizes video file paths in the database with actual files on disk, updating broken or missing paths as needed.
        
        Scans the specified storage directory for video files, matches them to database records by UUID, and updates the `raw_file` field for videos whose stored path is missing or incorrect. Supports dry-run and verbose modes, and can process all videos or a specific video by ID.
        """
        dry_run = options['dry_run']
        verbose = options['verbose']
        video_id = options.get('video_id')

        # Determine storage_dir from argument, env, or fallback
        storage_dir = options.get('storage_dir') or \
            os.environ.get('ENDOREG_STORAGE_DIR') or \
            './storage'
        storage_dir = Path(storage_dir)
        
        # Find all actual video files
        actual_files = {}
        for pattern in ['**/*.mp4', '**/*.avi', '**/*.mov', '**/*.mkv']:
            for file_path in storage_dir.glob(pattern):
                if file_path.is_file() and file_path.stat().st_size > 0:
                    # Extract UUID from filename
                    filename = file_path.name
                    # UUID is typically the first part before underscore or the whole name
                    if '_' in filename:
                        uuid_part = filename.split('_')[0]
                    else:
                        uuid_part = filename.split('.')[0]
                    
                    # Store relative path from storage directory
                    relative_path = file_path.relative_to(storage_dir)
                    actual_files[uuid_part] = {
                        'absolute_path': file_path,
                        'relative_path': relative_path,
                        'size_mb': file_path.stat().st_size / (1024*1024)
                    }

        self.stdout.write(f"Found {len(actual_files)} video files in storage")

        # Query videos to fix
        if video_id:
            try:
                videos = [VideoFile.objects.get(pk=video_id)]
                self.stdout.write(f"Processing specific video ID: {video_id}")
            except VideoFile.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"Video with ID {video_id} not found"))
                return
        else:
            videos = VideoFile.objects.all()
            self.stdout.write(f"Processing {videos.count()} videos...")

        fixed_count = 0
        skipped_count = 0
        error_count = 0

        for video in videos:
            try:
                uuid_str = str(video.uuid)
                
                # Check if we have a matching file
                if uuid_str in actual_files:
                    file_info = actual_files[uuid_str]
                    
                    # Check current file path
                    current_path_exists = False
                    current_path = None
                    
                    if hasattr(video, 'raw_file') and video.raw_file:
                        try:
                            current_path = Path(video.raw_file.path)
                            current_path_exists = current_path.exists()
                        except (ValueError, AttributeError, OSError):
                            current_path_exists = False
                    
                    if not current_path_exists:
                        # File path is broken, fix it
                        if verbose:
                            self.stdout.write(f"Video {video.id} ({uuid_str}):")
                            self.stdout.write(f"  Current: {current_path or 'None'} (broken)")
                            self.stdout.write(f"  Found: {file_info['absolute_path']} ({file_info['size_mb']:.1f} MB)")
                        
                        if not dry_run:
                            with transaction.atomic():
                                # Update the raw_file path
                                video.raw_file.name = str(file_info['relative_path'])
                                video.save(update_fields=['raw_file'])
                                
                            self.stdout.write(
                                self.style.SUCCESS(f"✅ Fixed video {video.id}: {file_info['relative_path']}")
                            )
                        else:
                            self.stdout.write(
                                self.style.WARNING(f"🔄 Would fix video {video.id}: {file_info['relative_path']}")
                            )
                        
                        fixed_count += 1
                    else:
                        if verbose:
                            self.stdout.write(f"✅ Video {video.id} ({uuid_str}) already has correct path")
                        skipped_count += 1
                else:
                    if verbose:
                        self.stdout.write(f"❌ Video {video.id} ({uuid_str}): No matching file found")
                    error_count += 1

            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f"❌ Error processing video {video.id}: {e}")
                )
                error_count += 1

        # Summary
        self.stdout.write(f"\n{'='*50}")
        self.stdout.write(self.style.SUCCESS("SUMMARY"))
        self.stdout.write(f"{'='*50}")
        
        action_word = "Would fix" if dry_run else "Fixed"
        self.stdout.write(f"🔧 {action_word}: {fixed_count} videos")
        self.stdout.write(f"✅ Already correct: {skipped_count} videos")
        self.stdout.write(f"❌ Errors/Missing files: {error_count} videos")
        
        if dry_run and fixed_count > 0:
            self.stdout.write("\n💡 Run without --dry-run to apply changes")
        elif not dry_run and fixed_count > 0:
            self.stdout.write(f"\n🎉 Successfully fixed {fixed_count} video file paths!")
            self.stdout.write("🔄 Restart your Django server to reload file paths")
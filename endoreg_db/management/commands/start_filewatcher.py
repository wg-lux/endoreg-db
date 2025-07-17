"""
Django Management Command for File Watcher Service

This command provides Django integration for the file watcher service.
"""

from django.core.management.base import BaseCommand
from django.conf import settings
import sys
import os
from pathlib import Path


class Command(BaseCommand):
    help = """
    Start the file watcher service for automatic video and PDF processing.
    
    This command monitors:
    - data/raw_videos/ for video files (.mp4, .avi, .mov, .mkv, .webm, .m4v)
    - data/raw_pdfs/ for PDF files (.pdf)
    
    When files are detected, they are automatically processed with:
    - Video: Import, anonymization, and segmentation
    - PDF: Import and anonymization
    """

    def add_arguments(self, parser):
        parser.add_argument(
            '--test',
            action='store_true',
            help='Test the file watcher configuration without starting monitoring',
        )
        parser.add_argument(
            '--existing',
            action='store_true',
            help='Process existing files in the directories before starting monitoring',
        )
        parser.add_argument(
            '--log-level',
            choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
            default='INFO',
            help='Set logging level (default: INFO)',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("Starting File Watcher Service"))
        
        # Set environment variables
        os.environ['WATCHER_LOG_LEVEL'] = options['log_level']
        
        try:
            # Add project root to path
            project_root = Path(settings.BASE_DIR)
            if str(project_root) not in sys.path:
                sys.path.insert(0, str(project_root))
            
            # Import the file watcher service from the correct location
            file_watcher_path = project_root / 'scripts' / 'file_watcher.py'
            
            if not file_watcher_path.exists():
                self.stdout.write(self.style.ERROR(f"❌ File watcher script not found: {file_watcher_path}"))
                return
            
            # Import the module dynamically
            import importlib.util
            spec = importlib.util.spec_from_file_location("file_watcher", file_watcher_path)
            file_watcher_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(file_watcher_module)
            
            FileWatcherService = file_watcher_module.FileWatcherService
            
            if options['test']:
                self.stdout.write("Testing file watcher configuration...")
                service = FileWatcherService()
                try:
                    service._validate_django_setup()
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"❌ Validation failed: {e}"))
                    return
                self.stdout.write(self.style.SUCCESS("✅ File watcher test passed"))
                return
            
            # Create and start the service
            service = FileWatcherService()
            
            if options['existing']:
                self.stdout.write("Processing existing files...")
                service._process_existing_files()
                self.stdout.write(self.style.SUCCESS("✅ Existing files processed"))
                return
            
            self.stdout.write("Starting file monitoring...")
            self.stdout.write(f"Video directory: {service.video_dir}")
            self.stdout.write(f"PDF directory: {service.pdf_dir}")
            self.stdout.write("Press Ctrl+C to stop")
            
            service.start()
            
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("\n⚠️  File watcher stopped by user"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Error: {str(e)}"))
            if options['verbosity'] >= 2:
                import traceback
                self.stdout.write(traceback.format_exc())
            sys.exit(1)
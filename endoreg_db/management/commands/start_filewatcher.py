import os
import sys

from django.core.management.base import BaseCommand

from endoreg_db.services.file_watcher import FileWatcherService


class Command(BaseCommand):
    help = (
        "Start the packaged file watcher service for automatic video and report "
        "processing."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--test",
            action="store_true",
            help="Test the file watcher configuration without starting monitoring",
        )
        parser.add_argument(
            "--existing",
            action="store_true",
            help="Process existing files in the directories before starting monitoring",
        )
        parser.add_argument(
            "--log-level",
            choices=["DEBUG", "INFO", "WARNING", "ERROR"],
            default="INFO",
            help="Set logging level (default: INFO)",
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("Starting File Watcher Service"))
        os.environ["WATCHER_LOG_LEVEL"] = options["log_level"]

        try:
            if options["test"]:
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

            if options["existing"]:
                self.stdout.write("Processing existing files...")
                service._process_existing_files()
                self.stdout.write(self.style.SUCCESS("✅ Existing files processed"))
                return

            self.stdout.write("Starting file monitoring...")
            self.stdout.write(f"Video directory: {service.video_dir}")
            self.stdout.write(f"report directory: {service.report_dir}")
            self.stdout.write("Press Ctrl+C to stop")

            service.start()

        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("\n⚠️  File watcher stopped by user"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Error: {str(e)}"))
            if options["verbosity"] >= 2:
                import traceback

                self.stdout.write(traceback.format_exc())
            sys.exit(1)

"""
Django management command to validate video file existence and accessibility.
"""

import logging
from collections.abc import Iterable
from typing import Literal, cast

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db.models.fields.files import FieldFile
from pydantic import ValidationError

from endoreg_db.models import VideoFile
from endoreg_db.utils.storage import field_file_is_readable, file_exists
from endoreg_db.utils.storage.streaming import field_file_size
from lx_dtypes.models.contracts.management_command import (
    ValidateVideoFileStatusPayload,
    ValidateVideoFilesCommandOptionsPayload,
)

logger = logging.getLogger(__name__)

# TODO Review if this is still used. Delete if not.


class Command(BaseCommand):
    help = "Validate video file existence and accessibility"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--video-id",
            type=int,
            help="Check specific video ID",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Enable verbose output",
        )

    def handle(self, *args: object, **options: object) -> None:
        """Validate video files and their accessibility."""
        try:
            options_payload = ValidateVideoFilesCommandOptionsPayload.model_validate(
                options
            )
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc

        verbose = options_payload.verbose
        video_id = options_payload.video_id
        fix_missing = options_payload.fix_missing

        if verbose:
            self.stdout.write(self.style.SUCCESS("Starting video validation..."))

        # Query videos
        if video_id:
            try:
                videos: Iterable[VideoFile] = [VideoFile.objects.get(pk=video_id)]
                self.stdout.write(f"Checking specific video ID: {video_id}")
            except VideoFile.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(f"Video with ID {video_id} not found")
                )
                return
        else:
            queryset = VideoFile.objects.all()
            videos = queryset
            self.stdout.write(f"Checking {queryset.count()} videos...")

        missing_files: list[ValidateVideoFileStatusPayload] = []
        accessible_files: list[ValidateVideoFileStatusPayload] = []
        corrupted_files: list[ValidateVideoFileStatusPayload] = []

        for video in videos:
            video_status = self.check_video_file(video, verbose)

            if video_status.status == "missing":
                missing_files.append(video_status)
            elif video_status.status == "corrupted":
                corrupted_files.append(video_status)
            else:
                accessible_files.append(video_status)

        # Report results
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(self.style.SUCCESS("VALIDATION COMPLETE"))
        self.stdout.write("=" * 60)

        self.stdout.write(f"✅ Accessible videos: {len(accessible_files)}")
        self.stdout.write(f"❌ Missing files: {len(missing_files)}")
        self.stdout.write(f"⚠️  Potentially corrupted: {len(corrupted_files)}")

        if missing_files:
            self.stdout.write(self.style.WARNING("\nMISSING FILES:"))
            for file_info in missing_files:
                self.stdout.write(
                    f"  - Video ID {file_info.video_id}: {file_info.error}"
                )
                if fix_missing:
                    self.stdout.write("    → Marking as inactive (if applicable)")

        if corrupted_files:
            self.stdout.write(self.style.WARNING("\nPOTENTIALLY CORRUPTED FILES:"))
            for file_info in corrupted_files:
                self.stdout.write(
                    f"  - Video ID {file_info.video_id}: {file_info.error}"
                )

        if verbose and accessible_files:
            self.stdout.write(self.style.SUCCESS("\nACCESSIBLE FILES:"))
            for file_info in accessible_files[:10]:  # Show first 10
                self.stdout.write(
                    f"  ✅ Video ID {file_info.video_id}: {file_info.path} ({file_info.size_mb:.1f} MB)"
                )

            if len(accessible_files) > 10:
                self.stdout.write(f"  ... and {len(accessible_files) - 10} more")

    def check_video_file(
        self, video: VideoFile, verbose: bool = False
    ) -> ValidateVideoFileStatusPayload:
        """
        Check a single video file for existence and basic accessibility.

        Returns:
            dict: Status information about the video file
        """
        del verbose
        video_info = self._base_video_status(video)

        def _check_field_file(
            attr: Literal["processed_file", "raw_file"], label: str
        ) -> tuple[bool, ValidateVideoFileStatusPayload]:
            file_field_object: object = getattr(video, attr, None)
            if not file_field_object or not getattr(file_field_object, "name", None):
                return False, video_info
            file_field = cast(FieldFile, file_field_object)
            path = str(file_field.name)
            try:
                if not file_exists(file_field):
                    return True, video_info.model_copy(
                        update={
                            "status": "missing",
                            "path": path,
                            "error": f"{label} does not exist in storage: {file_field.name}",
                        }
                    )
                file_size = field_file_size(file_field)
                size_mb = file_size / (1024 * 1024)
                if file_size == 0:
                    return True, video_info.model_copy(
                        update={
                            "status": "corrupted",
                            "path": path,
                            "size_mb": size_mb,
                            "error": f"{label} exists but has zero size",
                        }
                    )
                elif not field_file_is_readable(file_field):
                    return True, video_info.model_copy(
                        update={
                            "status": "corrupted",
                            "path": path,
                            "size_mb": size_mb,
                            "error": f"{label} exists but could not be materialized",
                        }
                    )
                return True, video_info.model_copy(
                    update={
                        "status": "accessible",
                        "path": path,
                        "size_mb": size_mb,
                    }
                )
            except Exception as e:
                return True, video_info.model_copy(
                    update={
                        "status": "corrupted",
                        "path": path,
                        "error": f"Cannot access {label}: {e}",
                    }
                )

        # Try each file attribute in order of preference
        found, result = _check_field_file("processed_file", "Processed file")
        if found:
            return result
        found, result = _check_field_file("raw_file", "Raw file")
        if found:
            return result

        # If none found
        return video_info.model_copy(
            update={
                "status": "missing",
                "error": "No video file paths found (no active_file, raw_file, or processed_file)",
            }
        )

    def _base_video_status(self, video: VideoFile) -> ValidateVideoFileStatusPayload:
        video_pk: object = getattr(video, "pk", 0)
        video_hash: object = getattr(video, "video_hash", "")
        video_id = video_pk if isinstance(video_pk, int) else 0
        return ValidateVideoFileStatusPayload(
            video_id=video_id,
            video_uuid=str(video_hash),
            status="unknown",
            size_mb=0.0,
        )

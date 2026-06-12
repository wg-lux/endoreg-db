#!/usr/bin/env python3
"""
Simple video file existence checker and path corrector for VideoFile records.
"""

import argparse
import os
import sys
from pathlib import Path

from endoreg_db.config.env import DEFAULT_DJANGO_SETTINGS_MODULE
from endoreg_db.utils.paths import STORAGE_DIR

VideoFile = None
file_exists = None
field_file_is_readable = None
field_file_size = None
django_available = False

# Parse command-line arguments and environment variables for configuration
parser = argparse.ArgumentParser(
    description="Simple video file existence checker and path corrector for VideoFile records."
)
parser.add_argument(
    "--django-base",
    type=str,
    default=os.environ.get(
        "ENDOREG_DJANGO_PROJECT_PATH",
        str(Path(__file__).resolve().parents[3]),
    ),
    help="Path to the Django project base (default: env ENDOREG_DJANGO_PROJECT_PATH or project root)",
)
parser.add_argument(
    "--django-settings",
    type=str,
    default=os.environ.get(
        "DJANGO_SETTINGS_MODULE",
        DEFAULT_DJANGO_SETTINGS_MODULE,
    ),
    help=(
        "Django settings module (default: env DJANGO_SETTINGS_MODULE or "
        f"{DEFAULT_DJANGO_SETTINGS_MODULE})"
    ),
)
parser.add_argument(
    "--storage-dir",
    type=str,
    default=str(STORAGE_DIR),
    help=f"Path to the storage directory (default: {STORAGE_DIR})",
)
args, unknown = parser.parse_known_args()

sys.path.insert(0, args.django_base)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", args.django_settings)

try:
    import django

    django.setup()
    from endoreg_db.models import VideoFile as _VideoFile
    from endoreg_db.utils.storage import (
        field_file_is_readable as _field_file_is_readable,
    )
    from endoreg_db.utils.storage import file_exists as _file_exists
    from endoreg_db.utils.storage_streaming import field_file_size as _field_file_size

    VideoFile = _VideoFile
    file_exists = _file_exists
    field_file_is_readable = _field_file_is_readable
    field_file_size = _field_file_size
    django_available = True
except Exception as e:
    print(f"Django not available: {e}")


def find_video_files() -> list[Path]:
    """Find all video files in storage directory."""
    storage_dir = Path(args.storage_dir)
    video_files: list[Path] = []

    for pattern in ["**/*.mp4", "**/*.avi", "**/*.mov", "**/*.mkv"]:
        video_files.extend(storage_dir.glob(pattern))

    return video_files


def check_video_file_accessibility(file_path: Path) -> tuple[bool, str]:
    """Check if a video file is accessible and valid."""
    try:
        if not file_path.exists():
            return False, "File does not exist"

        if file_path.stat().st_size == 0:
            return False, "File is empty (0 bytes)"

        if not os.access(file_path, os.R_OK):
            return False, "File is not readable"

        # Try to read first few bytes to check if it's actually a file
        with open(file_path, "rb") as f:
            header = f.read(8)
            if len(header) < 8:
                return False, "File too small or corrupted"

        return True, f"OK - {file_path.stat().st_size / (1024 * 1024):.1f} MB"

    except Exception as e:
        return False, f"Error checking file: {e}"


def main() -> None:
    print("🔍 VIDEO FILE EXISTENCE CHECKER")
    print("=" * 40)

    # Find all video files
    print("1. Scanning for video files...")
    video_files = find_video_files()
    print(f"Found {len(video_files)} video files in storage directory")

    if not video_files:
        print("❌ No video files found in storage directory!")
        return

    # Check each file
    print("\n2. Checking file accessibility...")
    accessible_files: list[Path] = []

    for video_file in video_files[:10]:  # Check first 10
        accessible, message = check_video_file_accessibility(video_file)
        status = "✅" if accessible else "❌"
        print(f"{status} {video_file.name}: {message}")

        if accessible:
            accessible_files.append(video_file)

    if not accessible_files:
        print("\n❌ No accessible video files found!")
        return

    print(f"\n✅ Found {len(accessible_files)} accessible video files")

    # If Django is available, check database records
    if django_available:
        print("\n3. Checking database records...")
        try:
            video_file_model = VideoFile
            if video_file_model is None:
                print("❌ Video model unavailable")
                return
            video_5 = video_file_model.objects.get(pk=5)
            print("📋 Video ID 5 found in database:")
            print(f"   UUID: {video_5.video_hash}")

            # Check different file path attributes
            for attr in ["raw_file", "processed_file"]:
                if hasattr(video_5, attr):
                    file_field = getattr(video_5, attr)
                    if file_field and getattr(file_field, "name", None):
                        try:
                            accessible = bool(
                                file_exists and file_exists(file_field)
                            ) and bool(
                                field_file_is_readable
                                and field_file_is_readable(file_field)
                            )
                            size_mb = (
                                field_file_size(file_field) if field_file_size else 0
                            ) / (1024 * 1024)
                            message = f"OK - {size_mb:.1f} MB"
                            status = "✅" if accessible else "❌"
                            print(f"   {attr}: {status} {file_field.name} ({message})")
                        except Exception as e:
                            print(f"   {attr}: ❌ Error accessing storage object: {e}")
                    else:
                        print(f"   {attr}: ❌ No file set")

            # Check if UUID matches any found files
            uuid_str = str(video_5.video_hash)
            matching_files: list[Path] = [
                f for f in accessible_files if uuid_str in str(f)
            ]

            if matching_files:
                print(f"\n💡 Found matching files for UUID {uuid_str}:")
                for match in matching_files:
                    accessible, message = check_video_file_accessibility(match)
                    print(f"   ✅ {match} ({message})")

                print("\n🔧 SOLUTION: Update VideoFile record to use:")
                print(f"   {matching_files[0]}")
                print("\n🐍 Django command to fix:")
                print("   video = VideoFile.objects.get(pk=5)")
                print(
                    f"   video.raw_file.name = '{matching_files[0].relative_to(Path(args.storage_dir))}'"
                )
                print("   video.save()")
            else:
                print(f"\n❌ No files found matching UUID {uuid_str}")

        except Exception as e:
            print(f"❌ Error checking database: {e}")

    print("\n4. 🎯 QUICK TEST RECOMMENDATION:")
    print("   Use this accessible file for testing:")
    print(f"   {accessible_files[0]}")
    print(f"   Size: {accessible_files[0].stat().st_size / (1024 * 1024):.1f} MB")


if __name__ == "__main__":
    main()

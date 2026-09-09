#!/usr/bin/env python3
"""
Debug script to test if asset files are being deleted during video creation.
"""

import os
import sys
from pathlib import Path

import django

# Add the project root to Python path
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

# Set Django settings
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.settings_test")

# Setup Django
django.setup()

from django.conf import settings

from tests.helpers.data_loader import load_base_db_data


def check_asset_deletion():
    """Test if asset files are deleted during video creation."""

    print("DEBUG: Checking asset deletion...")

    # Load base database data
    load_base_db_data()

    # Get initial asset files
    asset_dir = settings.ASSET_DIR
    print(f"ASSET_DIR: {asset_dir}")

    initial_files = list(asset_dir.glob("*.mp4"))
    print(f"Initial video assets: {[f.name for f in initial_files]}")

    if not initial_files:
        print("ERROR: No video assets found!")
        return


if __name__ == "__main__":
    check_asset_deletion()

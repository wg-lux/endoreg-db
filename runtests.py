#!/usr/bin/env python
import os
import sys
import argparse
from pathlib import Path

import django
from django.conf import settings
from django.test.utils import get_runner

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Django tests for specific subdirectories.")
    parser.add_argument(
        "dirs",
        metavar="DIR",
        type=str,
        nargs="*",
        help="Optional list of subdirectories under tests/ to run.",
    )
    args = parser.parse_args()

    tests_dir = Path(__file__).parent / "tests"

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test")
    django.setup()
    TestRunner = get_runner(settings)
    test_runner = TestRunner()

    test_labels = []
    if args.dirs:
        valid_dirs_found = False
        for dir_name in args.dirs:
            # Handle potential nested paths like "media/video"
            dir_path = tests_dir / dir_name
            module_path = tests_dir / (dir_name + ".py")
            if dir_path.is_dir() or module_path.is_file():
                # Convert path separator to module separator for the label
                test_label = f"tests.{dir_name.replace(os.sep, '.')}"
                test_labels.append(test_label)
                valid_dirs_found = True
            else:
                print(f"Warning: Directory 'tests/{dir_name}' not found. Skipping.", file=sys.stderr)

        if not valid_dirs_found:
            print("Error: No valid test directories specified.", file=sys.stderr)
            sys.exit(1)

    else:
        default_dirs = ["administration", "dataloader", "label", "media"]
        for dir_name in default_dirs:
            dir_path = tests_dir / dir_name
            if dir_path.is_dir():
                test_labels.append(f"tests.{dir_name}")
            else:
                print(f"Warning: Default directory 'tests/{dir_name}' not found. Skipping.", file=sys.stderr)
        if not test_labels:
            print("Error: None of the default test directories found.", file=sys.stderr)
            sys.exit(1)

    print(f"Running tests for: {', '.join(test_labels)}")
    failures = test_runner.run_tests(test_labels)
    sys.exit(bool(failures))
#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""

import os
import sys

DJANGO_SETTINGS_MODULE = "dev.dev_settings"
os.environ.setdefault("DJANGO_SETTINGS_MODULE", DJANGO_SETTINGS_MODULE)


def main():
    """Run Django administrative tasks.
    
    Imports and executes Django’s command-line management utility using system arguments.
    Raises an ImportError if Django is not installed or the virtual environment is not activated.
    """

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()

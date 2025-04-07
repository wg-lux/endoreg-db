#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""

import os
import sys

DJANGO_SETTINGS_MODULE = "dev.dev_settings"
os.environ.setdefault("DJANGO_SETTINGS_MODULE", DJANGO_SETTINGS_MODULE)


def main():
    """
    Run Django administrative tasks.
    
    This function imports Django's command-line utility and executes it with the current
    command-line arguments, serving as the entry point for administering the Django project.
    If Django is not installed or the PYTHONPATH is misconfigured, an ImportError is raised
    with instructions to verify the installation and virtual environment.
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

#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""

import os
import sys

DJANGO_SETTINGS_MODULE = "dev.dev_settings"
os.environ.setdefault("DJANGO_SETTINGS_MODULE", DJANGO_SETTINGS_MODULE)


def main():
    """Execute Django administrative tasks from the command line.
    
    This function serves as the entry point for Django's administrative commands.
    It attempts to import Django's command-line utility and execute commands based
    on the arguments provided via sys.argv. If Django is not installed or cannot be
    accessed, an ImportError is raised with instructions to verify the installation
    and virtual environment configuration.
    
    Raises:
        ImportError: If Django's management module cannot be imported.
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

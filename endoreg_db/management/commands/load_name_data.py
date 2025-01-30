# endoreg_db/management/commands/generate_names.py

import os
from django.core.management.base import BaseCommand
from endoreg_db.utils import collect_center_names  # Import your function here
from pathlib import Path

class Command(BaseCommand):
    help = "Generate first_names.yaml and last_names.yaml from center data"

    def add_arguments(self, parser):
        # Adding an argument for the input file path
        # parser.add_argument(
        #     '--input_file_path',
        #     type=str,
        #     required=False,
        #     help="Path to the input YAML file containing center data",
        #     default="data/center/data.yaml",
        # )
        # parser.add_argument(
        #     '--output-dir',
        #     type=str,
        #     default=os.getcwd(),
        #     help="Directory where the output files will be saved (default: current directory)",
        # )
        pass

    def handle(self, *args, **options):

        # Run the function with the provided arguments
        try:
            collect_center_names()
            self.stdout.write(self.style.SUCCESS("Successfully generated YAML files."))
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"An error occurred: {e}"))

            self.stderr.write(self.style.ERROR(f"PWD: {os.getcwd()}"))

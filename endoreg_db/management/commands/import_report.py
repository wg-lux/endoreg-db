import glob
import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import NoneType
from typing import Protocol, TypeAlias, TypedDict, Unpack, cast

import requests
from django.core.management.base import BaseCommand, CommandParser

from endoreg_db.helpers.data_load_orchestrator import load_all_reference_data
from endoreg_db.services.report_import import ReportImportService
from endoreg_db.utils.filesystem.file_operations import ensure_directory


# python manage.py import_report tests/assets/lux-gastro-report.pdf --verbose --start_ollama
# Dynamic import path manipulation to ensure local development version is used
JsonNull: TypeAlias = NoneType


class ImportReportOptions(TypedDict):
    verbose: bool
    file_path: str
    center_name: str
    report_dir_root: str
    save: bool
    start_ollama: bool
    ollama_debug: bool
    ollama_timeout: int


class _InitOllamaService(Protocol):
    def __call__(self, *, auto_start: bool) -> None: ...


class _OllamaServiceModule(Protocol):
    init_ollama_service: _InitOllamaService


class _PkCarrier(Protocol):
    pk: int | JsonNull


class _NamedField(Protocol):
    name: str | JsonNull


class _ImportedReport(Protocol):
    pk: int | JsonNull
    pdf_hash: str
    text: str | JsonNull
    anonymized_text: str | JsonNull
    sensitive_meta: _PkCarrier | JsonNull
    file: _NamedField
    processed_file: _NamedField

    def refresh_from_db(self) -> None: ...


def ensure_local_lx_anonymizer() -> bool:
    """
    Checks for a local development version of the lx-anonymizer package and adds it to sys.path if available.

    Returns:
        True if the local lx-anonymizer directory was found and added to sys.path; False otherwise.
    """
    script_dir = Path(
        __file__
    ).parent.parent.parent.parent.parent  # /home/admin/dev/lx-annotate/endoreg-db
    local_lx_anonymizer_path = script_dir / "lx-anonymizer"

    if local_lx_anonymizer_path.exists() and local_lx_anonymizer_path.is_dir():
        # Add the directory containing lx_anonymizer to the Python path
        if str(local_lx_anonymizer_path) not in sys.path:
            sys.path.insert(0, str(local_lx_anonymizer_path))
            print(f"Using local lx-anonymizer from: {local_lx_anonymizer_path}")
            return True
    return False


def _load_init_ollama_service() -> _InitOllamaService | JsonNull:
    try:
        module = importlib.import_module("lx_anonymizer.ollama.ollama_service")
    except ImportError:
        print("Could not import init_ollama_service from local or installed lx_anonymizer")
        return None
    service_module = cast(_OllamaServiceModule, module)
    return service_module.init_ollama_service


# Try to use local version, fall back to installed version
local_version_available = ensure_local_lx_anonymizer()
init_ollama_service = _load_init_ollama_service()


class Command(BaseCommand):
    """Management Command to import a report file to the database"""

    help = """
        Imports a report file (.pdf or .txt) to the database.
        1. Get center by center name from db (default: university_hospital_wuerzburg)
    """

    def add_arguments(self, parser: CommandParser) -> None:
        """
        Defines command-line arguments for the import_report management command.

        Adds options for the report path, center, report directory, save behavior,
        and controls for initializing the Ollama LLM service.
        """
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Display verbose output for all commands",
        )

        parser.add_argument(
            "file_path",
            type=str,
            help="Path to the Report file to import",
        )
        parser.add_argument(
            "--center_name",
            type=str,
            default="university_hospital_wuerzburg",
            help="Name of the center to associate with the report",
        )

        parser.add_argument(
            "--report_dir_root",
            type=str,
            default="~/test-data/db_report_dir",
            help="Path to the report directory",
        )

        parser.add_argument(
            "--save",
            action="store_true",
            default=False,
            help="Persist the imported report",
        )

        parser.add_argument(
            "--start_ollama",
            action="store_true",
            default=False,
            help="Start Ollama server for LLM processing",
        )

        parser.add_argument(
            "--ollama_debug",
            action="store_true",
            default=False,
            help="Enable debug mode for Ollama",
        )

        parser.add_argument(
            "--ollama_timeout",
            type=int,
            default=30,
            help="Maximum time to wait for Ollama to start in seconds",
        )

    def handle(
        self,
        *args: str,
        **options: Unpack[ImportReportOptions],
    ) -> None:
        """
        Handles the import of a report report file into the database, with optional LLM service initialization and anonymization.

        This method validates input options, optionally starts the Ollama LLM service,
        ensures the required files and directories exist, runs the report import
        pipeline, and writes a compact summary.
        """
        # Load initial or prerequisite data for the application.
        # This may include loading default values, configurations, or lookup table data
        # necessary for the import process or other application functionalities.
        try:
            load_all_reference_data()
            self.stdout.write(self.style.SUCCESS("Successfully loaded initial data."))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to load initial data: {e}"))
            # Depending on the criticality of load_data(), you might want to exit or handle differently.
            # For now, we'll just log the error and continue.

        verbose = options["verbose"]
        center_name = options["center_name"]
        report_dir_root = options["report_dir_root"]
        file_path = options["file_path"]
        save = options["save"]
        start_ollama = options["start_ollama"]
        ollama_debug = options["ollama_debug"]
        ollama_timeout = options["ollama_timeout"]

        self.stdout.write(
            self.style.SUCCESS(f"Starting report import for {file_path}...")
        )

        if local_version_available:
            self.stdout.write(
                self.style.SUCCESS("Using local development version of lx-anonymizer")
            )

        ollama_proc: subprocess.Popen[bytes] | JsonNull = None
        try:
            # Initialize Ollama service if requested
            if start_ollama:
                self.stdout.write(self.style.SUCCESS("Initializing Ollama service..."))
                try:
                    # Set Ollama environment variables
                    os.environ["OLLAMA_MAX_WAIT_TIME"] = str(ollama_timeout)
                    os.environ["OLLAMA_DEBUG"] = "true" if ollama_debug else "false"

                    # Try to find Ollama binary location from env or common paths
                    ollama_bin = os.environ.get("OLLAMA_BIN")
                    if not ollama_bin:
                        # Try common Nix store paths first
                        for path in [
                            "/run/current-system/sw/bin/ollama",
                            "/nix/store/*/bin/ollama",
                        ]:
                            matches = glob.glob(path)
                            if matches:
                                ollama_bin = matches[0]
                                break

                    if ollama_bin:
                        self.stdout.write(
                            self.style.SUCCESS(f"Using Ollama binary at: {ollama_bin}")
                        )
                        os.environ["OLLAMA_BIN"] = ollama_bin

                    # Start Ollama server process if not already running
                    # Check if ollama is already running
                    try:
                        resp = requests.get(
                            "http://127.0.0.1:11434/api/version", timeout=1
                        )
                        if resp.status_code == 200:
                            self.stdout.write(
                                self.style.SUCCESS("Ollama is already running")
                            )
                        else:
                            self.stdout.write(
                                self.style.WARNING(
                                    f"Ollama returned status code {resp.status_code}"
                                )
                            )
                    except requests.exceptions.RequestException:
                        self.stdout.write(
                            self.style.WARNING(
                                "Ollama is not running, attempting to start..."
                            )
                        )
                        # Find ollama binary
                        ollama_path = ollama_bin or shutil.which("ollama")
                        if ollama_path:
                            self.stdout.write(
                                self.style.SUCCESS(
                                    f"Starting Ollama using {ollama_path}"
                                )
                            )
                            # Start ollama serve in background
                            ollama_proc = subprocess.Popen(
                                [ollama_path, "serve"],
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                start_new_session=True,
                            )
                            self.stdout.write(
                                self.style.SUCCESS(
                                    "Ollama server started in background"
                                )
                            )
                        else:
                            self.stdout.write(
                                self.style.ERROR("Ollama binary not found in PATH")
                            )

                    # Start the service with explicit initialization when available.
                    if init_ollama_service is not None:
                        init_ollama_service(auto_start=True)
                        self.stdout.write(
                            self.style.SUCCESS(
                                "Ollama service initialized successfully"
                            )
                        )
                    else:
                        self.stdout.write(
                            self.style.WARNING(
                                "Ollama integration unavailable; continuing without it"
                            )
                        )
                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(f"Failed to initialize Ollama service: {e}")
                    )
                    self.stdout.write(
                        self.style.WARNING(
                            "Continuing without Ollama - some features may not work"
                        )
                    )

            # Ensure the report file exists
            file_path = Path(file_path).expanduser()
            if not file_path.exists():
                self.stdout.write(
                    self.style.ERROR(f"Report file not found: {file_path}")
                )
                return

            # Ensure the report directory exists
            report_dir_root = Path(report_dir_root).expanduser()
            ensure_directory(report_dir_root)

            if save:
                self.stdout.write(
                    self.style.WARNING(
                        "--save is deprecated and ignored; the central service always persists."
                    )
                )

            # Use the central report import service
            self.stdout.write(
                self.style.SUCCESS(
                    f"Importing report via ReportImportService from {file_path}..."
                )
            )
            report_file_obj = ReportImportService().import_and_anonymize(
                file_path=file_path,
                center_name=center_name,
                retry=False,
            )
            if not report_file_obj:
                self.stdout.write(self.style.ERROR("Failed to import report."))
                return
            report = cast(_ImportedReport, report_file_obj)
            report.refresh_from_db()

            text_len = len(report.text or "")
            anonymized_text_len = len(report.anonymized_text or "")
            self.stdout.write(
                self.style.SUCCESS(
                    f"Imported report id={report.pk} hash={report.pdf_hash}"
                )
            )
            sensitive_meta = report.sensitive_meta
            sensitive_meta_id = sensitive_meta.pk if sensitive_meta is not None else None
            self.stdout.write(
                self.style.SUCCESS(
                    "Import summary: "
                    f"text_len={text_len}, "
                    f"anonymized_text_len={anonymized_text_len}, "
                    f"sensitive_meta_id={sensitive_meta_id}"
                )
            )
            if verbose:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Stored file={report.file.name} "
                        f"processed_file={report.processed_file.name}"
                    )
                )
        finally:
            # Clean up Ollama process if we started it
            if ollama_proc is not None:
                self.stdout.write(
                    self.style.SUCCESS("Cleaning up Ollama server process...")
                )
                try:
                    ollama_proc.terminate()
                    ollama_proc.wait(timeout=10)
                    self.stdout.write(
                        self.style.SUCCESS("Ollama server process terminated.")
                    )
                except Exception as e:
                    self.stdout.write(
                        self.style.WARNING(
                            f"Failed to terminate Ollama server process: {e}"
                        )
                    )

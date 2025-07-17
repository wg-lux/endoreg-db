from pathlib import Path
import os
import sys
from django.core.management import BaseCommand
from endoreg_db.models import (
    RawPdfFile,
    PdfType,
)
from endoreg_db.helpers.data_loader import (
    load_data
)
from icecream import ic
# python manage.py import_report tests/assets/lux-gastro-report.pdf --verbose --start_ollama
# Dynamic import path manipulation to ensure local development version is used
def ensure_local_lx_anonymizer():
    """
    Checks for a local development version of the lx-anonymizer package and adds it to sys.path if available.
    
    Returns:
        True if the local lx-anonymizer directory was found and added to sys.path; False otherwise.
    """
    script_dir = Path(__file__).parent.parent.parent.parent.parent  # /home/admin/dev/lx-annotate/endoreg-db
    local_lx_anonymizer_path = script_dir / "lx-anonymizer"
    
    if local_lx_anonymizer_path.exists() and local_lx_anonymizer_path.is_dir():
        # Add the directory containing lx_anonymizer to the Python path
        if str(local_lx_anonymizer_path) not in sys.path:
            sys.path.insert(0, str(local_lx_anonymizer_path))
            print(f"Using local lx-anonymizer from: {local_lx_anonymizer_path}")
            return True
    return False

# Try to use local version, fall back to installed version
local_version_available = ensure_local_lx_anonymizer()



# Now import from lx_anonymizer
try:
    from lx_anonymizer.ollama_service import ollama_service
except ImportError:
    print("Could not import init_ollama_service from local or installed lx_anonymizer")
    raise

class Command(BaseCommand):
    """Management Command to import a report file to the database"""

    help = """
        Imports a .pdf file to the database.
        1. Get center by center name from db (default: university_hospital_wuerzburg)
    """

    def add_arguments(self, parser):
        """
        Defines command-line arguments for the import_report management command.
        
        Adds options for specifying the report file path, center name, report directory, deletion and save behavior, and controls for initializing the Ollama LLM service.
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
            "--delete_source",
            action="store_true",
            default=False,
            help="Delete the source report file after importing",
        )

        parser.add_argument(
            "--save",
            action="store_true",
            default=False,
            help="Save the report object to the database",
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

    def handle(self, *args, **options):
        """
        Handles the import of a PDF report file into the database, with optional LLM service initialization and anonymization.
        
        This method validates input options, optionally starts the Ollama LLM service, ensures the existence of required files and directories, determines the report type, processes the PDF for text and metadata extraction, anonymizes content, and saves the resulting data to the database. Provides verbose output and error handling throughout the process.
        """
        # Load initial or prerequisite data for the application.
        # This may include loading default values, configurations, or lookup table data
        # necessary for the import process or other application functionalities.
        try:
            load_data()
            self.stdout.write(self.style.SUCCESS("Successfully loaded initial data."))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to load initial data: {e}"))
            # Depending on the criticality of load_data(), you might want to exit or handle differently.
            # For now, we'll just log the error and continue.
        
            
        verbose = options["verbose"]
        center_name = options["center_name"]
        report_dir_root = options["report_dir_root"]
        file_path = options["file_path"]
        delete_source = options["delete_source"]
        save = options["save"]
        start_ollama = options["start_ollama"]
        ollama_debug = options["ollama_debug"]
        ollama_timeout = options["ollama_timeout"]

        if not isinstance(delete_source, bool):
            raise ValueError("delete_source must be a boolean")

        self.stdout.write(self.style.SUCCESS(f"Starting report import for {file_path}..."))
        
        if local_version_available:
            self.stdout.write(self.style.SUCCESS("Using local development version of lx-anonymizer"))
        
        ollama_proc = None  # Track Ollama process if started
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
                        for path in ["/run/current-system/sw/bin/ollama", 
                                    "/nix/store/*/bin/ollama"]:
                            import glob
                            matches = glob.glob(path)
                            if matches:
                                ollama_bin = matches[0]
                                break
                    
                    if ollama_bin:
                        self.stdout.write(self.style.SUCCESS(f"Using Ollama binary at: {ollama_bin}"))
                        os.environ["OLLAMA_BIN"] = ollama_bin
                    
                    # Start Ollama server process if not already running
                    import subprocess
                    import shutil
                    
                    # Check if ollama is already running
                    try:
                        import requests
                        resp = requests.get("http://127.0.0.1:11434/api/version", timeout=1)
                        if resp.status_code == 200:
                            self.stdout.write(self.style.SUCCESS("Ollama is already running"))
                        else:
                            self.stdout.write(self.style.WARNING(f"Ollama returned status code {resp.status_code}"))
                    except requests.exceptions.RequestException:
                        self.stdout.write(self.style.WARNING("Ollama is not running, attempting to start..."))
                        # Find ollama binary
                        ollama_path = ollama_bin or shutil.which("ollama")
                        if ollama_path:
                            self.stdout.write(self.style.SUCCESS(f"Starting Ollama using {ollama_path}"))
                            # Start ollama serve in background
                            ollama_proc = subprocess.Popen([
                                ollama_path, "serve"
                            ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
                            self.stdout.write(self.style.SUCCESS("Ollama server started in background"))
                        else:
                            self.stdout.write(self.style.ERROR("Ollama binary not found in PATH"))
                    
                    # Start the service with explicit initialization
                    ollama_service(auto_start=True)
                    self.stdout.write(self.style.SUCCESS("Ollama service initialized successfully"))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Failed to initialize Ollama service: {e}"))
                    self.stdout.write(self.style.WARNING("Continuing without Ollama - some features may not work"))

            # Ensure the report file exists
            file_path = Path(file_path).expanduser()
            if not file_path.exists():
                self.stdout.write(self.style.ERROR(f"Report file not found: {file_path}"))
                return

            # Ensure the report directory exists
            report_dir_root = Path(report_dir_root).expanduser()
            report_dir_root.mkdir(parents=True, exist_ok=True)

            # Create the report file object
            self.stdout.write(self.style.SUCCESS(f"Creating RawPdfFile object from {file_path}..."))
            report_file_obj = RawPdfFile.create_from_file(
                file_path=file_path,
                center_name=center_name,
                delete_source=delete_source,
                save=save,
            )
            if not report_file_obj:
                self.stdout.write(self.style.ERROR("Failed to create RawPdfFile object."))
                return
            
            report_file_obj.anonymized = False
            
            # Assign pdfType to the report file object
            if "report" in file_path.name:
                pdf_type_name = "ukw-endoscopy-examination-report-generic"
            elif "histo" in file_path.name:
                pdf_type_name = "ukw-endoscopy-histology-report-generic"
            elif "AW_PA" in file_path.name:
                pdf_type_name = "rkh-endoscopy-histology-report-generic"
            elif "AW" in file_path.name:
                pdf_type_name = "rkh-endoscopy-examination-report-generic"
            else:
                raise ValueError(f"Unknown report type: {file_path.name}")

            self.stdout.write(self.style.SUCCESS(f"Using PDF type: {pdf_type_name}"))
            try:
                pdf_type = PdfType.objects.get(name=pdf_type_name)
            except PdfType.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"PdfType with name '{pdf_type_name}' does not exist."))
                return
            report_file_obj.pdf_type = pdf_type

            rr_config = report_file_obj.get_report_reader_config()
            pdf_path = report_file_obj.file.path
            
            # Import at this point to avoid initializing the module too early
            from lx_anonymizer import ReportReader
            self.stdout.write(self.style.SUCCESS("Creating ReportReader..."))
            rr = ReportReader(**rr_config)

            self.stdout.write(self.style.SUCCESS(f"Processing report: {pdf_path}"))
            text, anonymized_text, report_meta = rr.process_report(
                pdf_path, verbose=verbose
            )

            if verbose:
                ic(text, anonymized_text, report_meta)
                
            self.stdout.write(self.style.SUCCESS("Processing file..."))
            report_file_obj.process_file(text, anonymized_text, report_meta, verbose=verbose)
            
            sensitive_meta = report_file_obj.sensitive_meta
            if verbose:
                ic(report_file_obj.sensitive_meta)
                
            
            self.stdout.write(self.style.SUCCESS("Saving..."))
            sensitive_meta.save()
            if verbose:
                ic(sensitive_meta)
                
            report_file_obj.anonymized=True
        finally:
            # Clean up Ollama process if we started it
            if ollama_proc is not None:
                import signal
                self.stdout.write(self.style.SUCCESS("Cleaning up Ollama server process..."))
                try:
                    ollama_proc.terminate()
                    ollama_proc.wait(timeout=10)
                    self.stdout.write(self.style.SUCCESS("Ollama server process terminated."))
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"Failed to terminate Ollama server process: {e}"))
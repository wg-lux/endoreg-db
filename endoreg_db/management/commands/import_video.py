# See Pipe 1 video file function.
#

"""
Management command to import a video file into the database.
This command is designed to be run from the command line and takes various arguments
to specify the video file, center name, and other options.
"""

from django.core.management import BaseCommand
from django.core.files.base import ContentFile
from django.db import connection
from pathlib import Path
from endoreg_db.models import VideoFile
from endoreg_db.models.administration.center import Center
from endoreg_db.models.medical.hardware import EndoscopyProcessor
# #FIXME
# from endoreg_db.management.commands import validate_video

from endoreg_db.utils.video.ffmpeg_wrapper import check_ffmpeg_availability # ADDED

import logging
logger = logging.getLogger(__name__)

# Import frame cleaning functionality - simplified approach
FRAME_CLEANING_AVAILABLE = False

# Try to import lx_anonymizer using the existing working import method from create_from_file
try:
    # Check if we can find the lx-anonymizer directory
    current_file = Path(__file__)
    endoreg_db_root = current_file.parent.parent.parent.parent
    lx_anonymizer_path = endoreg_db_root / "lx-anonymizer"
    
    if lx_anonymizer_path.exists():
        # Add to Python path temporarily
        import sys
        if str(lx_anonymizer_path) not in sys.path:
            sys.path.insert(0, str(lx_anonymizer_path))
        
        # Try simple import
        from lx_anonymizer import FrameCleaner, ReportReader
        
        FRAME_CLEANING_AVAILABLE = True
        logger.debug("Successfully imported lx_anonymizer modules")
        
        # Remove from path to avoid conflicts
        if str(lx_anonymizer_path) in sys.path:
            sys.path.remove(str(lx_anonymizer_path))
            
except Exception as e:
    logger.debug(f"Frame cleaning not available: {e}")
    FRAME_CLEANING_AVAILABLE = False

IMPORT_MODELS = [
    VideoFile.__name__,
]

IMPORT_METADATA = {
    VideoFile.__name__: {
        "uuid": VideoFile.uuid,
        "raw_file": VideoFile.raw_file,
        "processed_file": VideoFile.processed_file,
        "foreign_keys": [],
        "foreign_key_models": [],
    },
}

class Command(BaseCommand):
    help = """
        Creates a new VideoFile object in the database.
        1. Validates the existence of the specified center and processor
        2. Checks that the video file is saved and anonymized
        3. Creates or updates a ModelMeta entry with the specified parameters
    """

    def add_arguments(self, parser):
        
        """
        Adds command-line arguments for the video import management command.
        
        Defines options for specifying the video file path, associated center and processor names, directory roots for frames and videos, deletion and saving behavior, model path, segmentation usage, and verbosity.
        """
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Display verbose output",
        )
        parser.add_argument(
            "--",
            type=str,
            default="university_hospital_wuerzburg",
            help="Name of the center to associate with video",
        )
        
        # add the path to the video file
        parser.add_argument(
            "video_file",
            type=Path,
            help="Path to the video file to import",
        )

        # frame dir parent
        parser.add_argument(
            "--frame_dir_root",
            type=str,
            default="~/test-data/raw_frame_dir",
            help="Path to the frame directory",
        )

        # video dir
        parser.add_argument(
            "--video_dir_root",
            type=str,
            default="~/test-data/raw_video_dir",
            help="Path to the video directory",
        )

        # delete source
        parser.add_argument(
            "--delete_source",
            action="store_true",
            default=False,
            help="Delete the source video file after importing",
        )

        # save video file
        parser.add_argument(
            "--save_video_file",
            action="store_true",
            default=True,
            help="Save the video file to the video directory",
        )

        # model_path
        parser.add_argument(
            "--model_name",
            type=str,
            default="image_multilabel_classification_colonoscopy_default",
            help="AiModel Name",
        )
        
        # 
        parser.add_argument(
            "--segmentation",
            action="store_true",
            help="Whether to use segmentation",
        )
        
        parser.add_argument(
            "--processor_name",
            type=str,
            default="olympus_cv_1500",
            help="Name of the processor to associate with video",
        )

    def handle(self, *args, **options):  
        
        """
        Imports a video file into the database, associating it with a specified medical center and endoscopy processor, and optionally applies AI-based segmentation.
        
        Checks for required dependencies, loads reference data, validates the existence of the specified center and processor, and processes the video file. If segmentation is enabled, retrieves the latest segmentation model metadata. Handles interactive processor selection if multiple are available, creates a new `VideoFile` entry, and invokes the processing pipeline. Can optionally delete the source file or save the video to a specified directory. Reports the outcome of the import and processing steps.
        """
        try: # ADDED
            check_ffmpeg_availability() # ADDED
            self.stdout.write(self.style.SUCCESS("FFMPEG is available")) # ADDED
        except FileNotFoundError as e: # ADDED
            self.stderr.write(self.style.ERROR(str(e))) # ADDED
            # Decide if the command should exit or if FFMPEG is optional for some operations
            # For this command, it seems FFMPEG is critical for VideoFile.pipe_1 and VideoFile.create_from_file
            return # ADDED
        
        self.stdout.write(f"Current database: {connection.alias}")
        self.stdout.write(self.style.SUCCESS("Starting video import..."))      

        # Should not be invoked here but in a previous db setup step
        # load_gender_data()
        # load_disease_data()
        # load_event_data()
        # load_information_source()
        # load_examination_data()
        # load_center_data()
        # load_endoscope_data()

        segmentation = options["segmentation"]

        verbose = options["verbose"]
        center_name = options["center_name"]
        video_file = options["video_file"]
        frame_dir_root = options["frame_dir_root"]
        video_dir_root = options["video_dir_root"]
        delete_source = options["delete_source"]
        save_video_file = options["save_video_file"]
        model_name = options["model_name"]
        processor_name = options["processor_name"]
        video_file = Path(video_file).expanduser()

        
        assert isinstance(delete_source, bool), "delete_source must be a boolean"
        assert isinstance(save_video_file, bool), "save_video_file must be a boolean"
        assert isinstance(verbose, bool), "verbose must be a boolean"
        assert isinstance(center_name, str), "center_name must be a string"
        assert isinstance(video_file, Path), "video_file must be a Path"
        assert isinstance(frame_dir_root, str), "frame_dir_root must be a string"
                # Assert Center exists -> Does not exist methods are deprecated
        try:
            center = Center.objects.get(name=center_name)
            self.stdout.write(self.style.SUCCESS(f"Using center: {center.name}"))
        except Center.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"Center not found: {center_name}"))
            return

        # Assert Processor Exists
        if processor_name is None:
            processors_qs = center.endoscopy_processors.all()
            proc_count = processors_qs.count()
            if proc_count == 0:
                raise AssertionError(
                    f"No processors linked to '{center.name}' and no processor called '{processor_name}' exists. "
                    "Fallback from default Processor applied."
                )
            elif proc_count == 1:
                processor = processors_qs.first()
            else:

                processor = self._choose_processor_interactively(processors_qs)
                self.stdout.write(self.style.SUCCESS(f"Using processor: {processor.name}"))
        
        else:
            processor = EndoscopyProcessor.objects.get(name=processor_name)

            cns = processor.centers.values_list("name", flat=True)
            if center_name not in cns:
                self.stdout.write(
                    self.style.ERROR(
                        f"Processor '{processor_name}' is not linked to center '{center_name}'."
                    )
                )
                return

        if not Path(video_file).exists():
            self.stdout.write(self.style.ERROR(f"Video file not found: {video_file} saving unsuccessful."))
            return AssertionError(f"Video file not found: {video_file}")
        
        # Create VideoFile instance first
        video_file_obj = VideoFile.create_from_file_initialized(
            file_path=video_file,
            center_name=center_name,
            processor_name=processor_name,
            delete_source=delete_source,
            save_video_file=save_video_file, # Add this line
        )
        
        if not video_file_obj:
            self.stdout.write(self.style.ERROR("Failed to create VideoFile instance"))
            return
        
        self.stdout.write(self.style.SUCCESS(f"Created VideoFile with UUID: {video_file_obj.uuid}"))
        
        # Initialize video specs and frames
        video_file_obj.initialize_video_specs()
        video_file_obj.initialize_frames()
        
        # Run Pipe 1 for OCR and AI processing
        self.stdout.write(self.style.SUCCESS("Starting Pipe 1 processing (OCR + AI)..."))
        success = video_file_obj.pipe_1(
            model_name=model_name,
            delete_frames_after=True,
            ocr_frame_fraction=0.01,
            ocr_cap=5
        )
        
        if not success:
            self.stdout.write(self.style.ERROR("Pipe 1 processing failed"))
            return
            
        self.stdout.write(self.style.SUCCESS("Pipe 1 processing completed"))
        
        # Ensure minimum patient data is available
        self.stdout.write(self.style.SUCCESS("Ensuring minimum patient data..."))
        self._ensure_default_patient_data(video_file_obj)
        
        # Frame-level anonymization integration
        if FRAME_CLEANING_AVAILABLE and video_file_obj.raw_file:
            try:
                self.stdout.write(self.style.SUCCESS("Starting frame-level anonymization..."))
                # Properly instantiate FrameCleaner and ReportReader with correct arguments
                frame_cleaner = FrameCleaner()
                report_reader = ReportReader(
                    report_root_path=str(video_file_obj.raw_file.path),
                    locale="de_DE",  # Default German locale for medical data
                    text_date_format="%d.%m.%Y"  # Common German date format
                )
                
                # Updated to handle new return signature (path, metadata)
                cleaned_video_path, extracted_metadata = frame_cleaner.clean_video(
                    video_path=Path(video_file_obj.raw_file.path),
                    endoscope_image_roi=video_file_obj.processor.get_roi_endoscope_image() if video_file_obj.processor else None,
                    endoscope_data_roi_nested=video_file_obj.processor.get_rois() if video_file_obj.processor else None,
                    output_path=video_file_obj.get_processed_file_path(),
                    technique="mask_overlay"  # Use mask overlay technique as default, if not set this will be inferred.
                )
                
                # Save the cleaned video using Django's FileField
                with open(cleaned_video_path, 'rb') as f:
                    video_file_obj.raw_file.save(cleaned_video_path.name, ContentFile(f.read()))
                video_file_obj.save()
                
                self.stdout.write(self.style.SUCCESS(f"Frame cleaning completed: {cleaned_video_path.name}"))
                self.stdout.write(self.style.SUCCESS(f"Extracted metadata: {extracted_metadata}"))
                
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"Frame cleaning failed, continuing with original video: {e}"))
        elif not FRAME_CLEANING_AVAILABLE:
            self.stdout.write(self.style.WARNING("Frame cleaning not available (lx_anonymizer not found)"))
        
        # Now call pipe_1 on the VideoFile instance
        if segmentation:
            success = video_file_obj.pipe_1(model_name=model_name, model_meta_version = None)

            if success:
                self.stdout.write(self.style.SUCCESS("Pipeline 1 completed successfully"))
            else:
                self.stdout.write(self.style.ERROR("Pipeline 1 failed"))
    
    def _ensure_default_patient_data(self, video_file):
        """
        Ensure video has minimum required patient data in SensitiveMeta.
        Creates default values if data is missing after OCR processing.
        """
        from endoreg_db.models import SensitiveMeta
        from datetime import date
        
        if not video_file.sensitive_meta:
            self.stdout.write(self.style.WARNING(f"No SensitiveMeta found for video {video_file.uuid}, creating default"))
            
            # Create default SensitiveMeta with placeholder data
            default_data = {
                "patient_first_name": None,
                "patient_last_name": None, 
                "patient_dob": None,
                "examination_date": None,
                "center_name": video_file.center.name if video_file.center else "unknown",
            }
            
            try:
                sensitive_meta = SensitiveMeta.create_from_dict(default_data)
                video_file.sensitive_meta = sensitive_meta
                video_file.save(update_fields=['sensitive_meta'])
                self.stdout.write(self.style.SUCCESS(f"Created default SensitiveMeta for video {video_file.uuid}"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Failed to create default SensitiveMeta for video {video_file.uuid}: {e}"))
                
        else:
            # Update existing SensitiveMeta with missing fields
            update_needed = False
            update_data = {}
            
            if not video_file.sensitive_meta.patient_first_name:
                update_data["patient_first_name"] = "Patient"
                update_needed = True
                
            if not video_file.sensitive_meta.patient_last_name:
                update_data["patient_last_name"] = "Unknown"
                update_needed = True
                
            if not video_file.sensitive_meta.patient_dob:
                update_data["patient_dob"] = date(1990, 1, 1)
                update_needed = True
                
            if not video_file.sensitive_meta.examination_date:
                update_data["examination_date"] = date.today()
                update_needed = True
                
            if update_needed:
                try:
                    video_file.sensitive_meta.update_from_dict(update_data)
                    self.stdout.write(self.style.SUCCESS(f"Updated missing SensitiveMeta fields for video {video_file.uuid}: {list(update_data.keys())}"))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Failed to update SensitiveMeta for video {video_file.uuid}: {e}"))
            else:
                self.stdout.write(self.style.SUCCESS(f"SensitiveMeta for video {video_file.uuid} already has all required fields"))
    
    def _choose_processor_interactively(
        self, processors_qs
    ) -> EndoscopyProcessor:
        """
        Interactively prompts the user to select an endoscopy processor from a list.
        
        Displays all available processors and requests user input until a valid selection is made. Aborts the process if the user interrupts input.
        
        Args:
            processors_qs: Queryset of EndoscopyProcessor objects to present for selection.
        
        Returns:
            The EndoscopyProcessor object chosen by the user.
        """
        # turn the QS into a concrete list so we can index it later
        processors = list(processors_qs)           # -> [EndoscopyProcessor, …]

        self.stdout.write(self.style.ERROR(
            f"\nThe centre has {len(processors)} endoscopy processors.\n"
            "Choose one for this import:\n"
        ))
        for idx, proc in enumerate(processors, 1):
            self.stdout.write(f"  [{idx}] {proc.name}")

        while True:                                # keep prompting until valid
            try:
                choice = input("Processor number › ").strip()
            except (EOFError, KeyboardInterrupt):
                self.stderr.write("\nAborted.")
                raise SystemExit(1)

            try:
                index = int(choice) - 1
                if not 0 <= index < len(processors):
                    raise ValueError
            except ValueError:
                self.stderr.write("❌  Please enter a number in the list above.\n")
                continue

            return processors[index]               # ← the chosen object

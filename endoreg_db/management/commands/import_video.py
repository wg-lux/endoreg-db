# See Pipe 1 video file function.
#

"""
Management command to import a video file into the database.
This command is designed to be run from the command line and takes various arguments
to specify the video file, center name, and other options.
"""
from django.core.management import BaseCommand
from django.db import connection
from pathlib import Path
from endoreg_db.models import VideoFile, ModelMeta
from endoreg_db.models.administration.center import Center
from endoreg_db.models.medical.hardware import EndoscopyProcessor
# #FIXME
# from endoreg_db.management.commands import validate_video

from ...helpers.default_objects import (
    get_latest_segmentation_model
)

from endoreg_db.utils.video.ffmpeg_wrapper import check_ffmpeg_availability # ADDED

from endoreg_db.helpers.data_loader import (
    load_disease_data,
    load_gender_data,
    load_event_data,
    load_information_source,
    load_examination_data,
    load_center_data,
    load_endoscope_data,
    load_ai_model_label_data,
    load_ai_model_data,
    load_default_ai_model
    
)

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
            "--center_name",
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
            "--model_path",
            type=str,
            default="./data/models/colo_segmentation_RegNetX800MF_6.ckpt",
            help="Path to the model file",
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
        Handles the import of a video file into the database, associating it with a specified medical center and endoscopy processor, and optionally applying AI-based segmentation.
        
        Checks for required dependencies (such as FFMPEG), loads reference data, validates the existence of the specified center and processor, and processes the video file. If segmentation is enabled, retrieves the latest segmentation model metadata. If multiple processors are linked to the center, prompts the user to select one interactively. Creates a new `VideoFile` database entry with the provided options, and can optionally delete the source file or save the video to a specified directory.
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

        load_gender_data()
        load_disease_data()
        load_event_data()
        load_information_source()
        load_examination_data()
        load_center_data()
        load_endoscope_data()

        segmentation = options["segmentation"]
        self.ai_model_meta = get_latest_segmentation_model()
        if segmentation:
            load_ai_model_label_data()
            load_ai_model_data()
            load_default_ai_model()
            # Fetch the associated ModelMeta instance
            try:
                # Assuming ModelMeta has a foreign key 'model' to AiModel
                self.ai_model_meta = get_latest_segmentation_model()
            except ModelMeta.DoesNotExist as exc:
                raise AssertionError("No ModelMeta found for the latest default segmentation AiModel") from exc

        verbose = options["verbose"]
        center_name = options["center_name"]
        video_file = options["video_file"]
        frame_dir_root = options["frame_dir_root"]
        video_dir_root = options["video_dir_root"]
        delete_source = options["delete_source"]
        save_video_file = options["save_video_file"]
        model_path = options["model_path"]
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
            self.stdout.write(self.style.SUCCESS(f"Using center: {center.name_en}"))
        except Center.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"Center not found: {center_name}"))
            return

        # Assert Processor Exists
        try:
            processors_qs = EndoscopyProcessor.objects.filter(centers=center)
            proc_count = processors_qs.count()
            if proc_count == 0:
                fallback_name = options.get("processor_name", "endoscope_processor")
                processor = EndoscopyProcessor.objects.filter(name=fallback_name).first()
                if processor is None:
                    self.stderr.write(
                        self.style.ERROR(
                            f"No processors linked to '{center.name}' and "
                            f"no processor called '{fallback_name}' exists. Fallback from default Processor applied."
                        )
                    )
                    processor = EndoscopyProcessor.objects.filter(name="olympus_cv_1500").first()

                processor.centers.add(center)
                self.stdout.write(
                    self.style.WARNING(
                        f"Linked fallback processor '{processor.name}' to centre '{center.name}'."
                    )
                )
                return

            elif proc_count == 1:
                processor = processors_qs.first()
            else:
                processor = self._choose_processor_interactively(processors_qs)

            self.stdout.write(self.style.SUCCESS(f"Using processor: {processor.name}"))
        except EndoscopyProcessor.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"Processor not found: {processor_name}"))
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
        )
        
        if not video_file_obj:
            self.stdout.write(self.style.ERROR("Failed to create VideoFile instance"))
            return
        
        # Now call pipe_1 on the VideoFile instance
        if self.ai_model_meta:
            success = video_file_obj.pipe_1(model_name=self.ai_model_meta.model.name)
        else:
            # Get the default model meta if segmentation is not enabled
            ai_model_meta = ModelMeta.objects.filter(
                model__name="colo_segmentation_RegNetX800MF_6"
            ).first()
            
            if ai_model_meta:
                success = video_file_obj.pipe_1(model_name=ai_model_meta.model.name)
            else:
                # Fallback to pipe_1 with default model name
                success = video_file_obj.pipe_1(model_name="colo_segmentation_RegNetX800MF_6")
        
        if success:
            self.stdout.write(self.style.SUCCESS("Pipeline 1 completed successfully"))
        else:
            self.stdout.write(self.style.ERROR("Pipeline 1 failed"))
            
    
    
    def _choose_processor_interactively(
        self, processors_qs
    ) -> EndoscopyProcessor:
        """
        Prompts the user to select an endoscopy processor from a list when multiple are available.
        
        Displays all processors associated with a center and repeatedly prompts the user to choose one by number. Aborts if the user interrupts input.
        
        Args:
            processors_qs: A queryset of EndoscopyProcessor objects to choose from.
        
        Returns:
            The selected EndoscopyProcessor object.
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
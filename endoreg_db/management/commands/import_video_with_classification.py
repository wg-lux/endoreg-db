"""
Management command to import a video file and automatically run NICE/PARIS classifications.
This command extends the basic import_video functionality with automatic classification.
"""
from django.core.management import BaseCommand, call_command
from django.db import connection
from pathlib import Path
from endoreg_db.models import VideoFile, ModelMeta
from endoreg_db.models.administration.center import Center
from endoreg_db.models.medical.hardware import EndoscopyProcessor

# TODO Migrate
from endoreg_db.serializers._old.Frames_NICE_and_PARIS_classifications import (
    ForNiceClassificationSerializer,
    ForParisClassificationSerializer
)

from ...helpers.default_objects import (
    get_latest_segmentation_model
)

from endoreg_db.utils.video.ffmpeg_wrapper import check_ffmpeg_availability

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


class Command(BaseCommand):
    help = """
        Imports a video file and automatically runs NICE/PARIS classifications.
        
        Workflow:
        1. Validates dependencies (FFMPEG, center, processor)
        2. Loads reference data
        3. Imports video using VideoFile.create_from_file_initialized()
        4. Runs VideoFile.pipe_1() for AI segmentation
        5. Automatically runs NICE classification
        6. Automatically runs PARIS classification (if frame_dir available)
        7. Reports results of all steps
    """

    def add_arguments(self, parser):
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
        
        parser.add_argument(
            "video_file",
            type=Path,
            help="Path to the video file to import",
        )

        parser.add_argument(
            "--frame_dir_root",
            type=str,
            default="~/test-data/raw_frame_dir",
            help="Path to the frame directory",
        )

        parser.add_argument(
            "--video_dir_root",
            type=str,
            default="~/test-data/raw_video_dir",
            help="Path to the video directory",
        )

        parser.add_argument(
            "--delete_source",
            action="store_true",
            default=False,
            help="Delete the source video file after importing",
        )

        parser.add_argument(
            "--save_video_file",
            action="store_true",
            default=True,
            help="Save the video file to the video directory",
        )

        parser.add_argument(
            "--model_path",
            type=str,
            default="./data/models/colo_segmentation_RegNetX800MF_6.safetensors",
            help="Path to the model file",
        )
        
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
        
        # NEW: Classification options
        parser.add_argument(
            "--skip_classification",
            action="store_true",
            default=False,
            help="Skip automatic NICE/PARIS classification after import",
        )
        
        parser.add_argument(
            "--classification_only",
            type=str,
            choices=['nice', 'paris', 'both'],
            default='both',
            help="Which classification types to run (default: both)",
        )

    def handle(self, *args, **options):
        """
        Main command handler that orchestrates the complete workflow.
        """
        # Check FFMPEG availability
        try:
            check_ffmpeg_availability()
            self.stdout.write(self.style.SUCCESS("✓ FFMPEG is available"))
        except FileNotFoundError as e:
            self.stderr.write(self.style.ERROR(f"✗ FFMPEG not found: {str(e)}"))
            return

        self.stdout.write(f"Database: {connection.alias}")
        self.stdout.write(self.style.SUCCESS("=== Starting Video Import and Classification Workflow ==="))

        # Load reference data
        self._load_reference_data()

        # Parse options
        segmentation = options["segmentation"]
        skip_classification = options["skip_classification"]
        classification_only = options["classification_only"]
        
        # Get AI model if segmentation is enabled
        self.ai_model_meta = None
        if segmentation:
            load_ai_model_label_data()
            load_ai_model_data()
            load_default_ai_model()
            try:
                self.ai_model_meta = get_latest_segmentation_model()
            except ModelMeta.DoesNotExist as exc:
                raise AssertionError("No ModelMeta found for the latest default segmentation AiModel") from exc

        # Validate input parameters
        video_file = Path(options["video_file"]).expanduser()
        if not video_file.exists():
            self.stdout.write(self.style.ERROR(f"✗ Video file not found: {video_file}"))
            return

        # Validate center and processor
        center, processor = self._validate_center_and_processor(options)
        if not center or not processor:
            return

        # Step 1: Import Video
        self.stdout.write(self.style.SUCCESS("\n=== Step 1: Importing Video ==="))
        video_file_obj = self._import_video(options, video_file)
        if not video_file_obj:
            return

        # Step 2: Run Pipeline 1 (AI Segmentation)
        self.stdout.write(self.style.SUCCESS("\n=== Step 2: Running AI Segmentation (Pipeline 1) ==="))
        pipeline_success = self._run_pipeline_1(video_file_obj)
        if not pipeline_success:
            self.stdout.write(self.style.WARNING("⚠ Pipeline 1 failed, but continuing with classification..."))

        # Step 3: Run Classifications (if not skipped)
        if not skip_classification:
            self.stdout.write(self.style.SUCCESS("\n=== Step 3: Running Polyp Classifications ==="))
            self._run_classifications(video_file_obj, classification_only)
        else:
            self.stdout.write(self.style.WARNING("⚠ Skipping classifications as requested"))

        self.stdout.write(self.style.SUCCESS("\n=== Workflow Complete ==="))
        self.stdout.write(f"✓ Video imported with ID: {video_file_obj.id}")
        self.stdout.write(f"✓ Video available at: {video_file_obj.processed_file}")

    def _load_reference_data(self):
        """Load all necessary reference data."""
        self.stdout.write("Loading reference data...")
        load_gender_data()
        load_disease_data()
        load_event_data()
        load_information_source()
        load_examination_data()
        load_center_data()
        load_endoscope_data()
        self.stdout.write(self.style.SUCCESS("✓ Reference data loaded"))

    def _validate_center_and_processor(self, options):
        """Validate that center and processor exist."""
        center_name = options["center_name"]
        processor_name = options["processor_name"]

        # Validate center
        try:
            center = Center.objects.get(name=center_name)
            self.stdout.write(self.style.SUCCESS(f"✓ Using center: {center.name_en}"))
        except Center.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"✗ Center not found: {center_name}"))
            return None, None

        # Validate processor
        try:
            processors_qs = EndoscopyProcessor.objects.filter(centers=center)
            proc_count = processors_qs.count()
            
            if proc_count == 0:
                processor = EndoscopyProcessor.objects.filter(name=processor_name).first()
                if processor is None:
                    self.stderr.write(self.style.ERROR(f"✗ No processors found for center or fallback"))
                    return None, None
                processor.centers.add(center)
                self.stdout.write(self.style.WARNING(f"⚠ Linked fallback processor '{processor.name}' to center"))
            elif proc_count == 1:
                processor = processors_qs.first()
            else:
                processor = self._choose_processor_interactively(processors_qs)

            self.stdout.write(self.style.SUCCESS(f"✓ Using processor: {processor.name}"))
            return center, processor

        except EndoscopyProcessor.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"✗ Processor not found: {processor_name}"))
            return None, None

    def _import_video(self, options, video_file):
        """Import the video file into the database."""
        try:
            video_file_obj = VideoFile.create_from_file_initialized(
                file_path=video_file,
                center_name=options["center_name"],
                processor_name=options["processor_name"],
                delete_source=options["delete_source"],
                save_video_file=options["save_video_file"],
            )
            
            if video_file_obj:
                self.stdout.write(self.style.SUCCESS(f"✓ Video imported successfully (ID: {video_file_obj.id})"))
                return video_file_obj
            else:
                self.stdout.write(self.style.ERROR("✗ Failed to create VideoFile instance"))
                return None
                
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"✗ Error importing video: {str(e)}"))
            return None

    def _run_pipeline_1(self, video_file_obj):
        """Run the AI segmentation pipeline."""
        try:
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
                    success = video_file_obj.pipe_1(model_name="colo_segmentation_RegNetX800MF_6")

            if success:
                self.stdout.write(self.style.SUCCESS("✓ Pipeline 1 completed successfully"))
                return True
            else:
                self.stdout.write(self.style.ERROR("✗ Pipeline 1 failed"))
                return False
                
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"✗ Pipeline 1 error: {str(e)}"))
            return False

    def _run_classifications(self, video_file_obj, classification_only):
        """Run NICE and/or PARIS classifications."""
        video_list = [video_file_obj]
        
        # Run NICE Classification
        if classification_only in ['nice', 'both']:
            self.stdout.write("Running NICE classification...")
            try:
                nice_serializer = ForNiceClassificationSerializer()
                nice_results = nice_serializer.to_representation(video_list)
                
                if nice_results and isinstance(nice_results, dict) and 'data' in nice_results:
                    segments_count = sum(len(item.get('frames', [])) for item in nice_results['data'] if 'frames' in item)
                    self.stdout.write(self.style.SUCCESS(f"✓ NICE classification completed ({segments_count} segments found)"))
                else:
                    self.stdout.write(self.style.WARNING("⚠ NICE classification completed but no valid segments found"))
                    
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"✗ NICE classification failed: {str(e)}"))

        # Run PARIS Classification
        if classification_only in ['paris', 'both']:
            self.stdout.write("Running PARIS classification...")
            try:
                # Check if video has frame_dir (required for PARIS)
                if not getattr(video_file_obj, "frame_dir", None):
                    self.stdout.write(self.style.WARNING("⚠ PARIS classification skipped: no frame_dir available"))
                else:
                    paris_serializer = ForParisClassificationSerializer()
                    paris_results = paris_serializer.to_representation(video_list)
                    
                    if paris_results and isinstance(paris_results, dict) and 'data' in paris_results:
                        segments_count = sum(len(item.get('frames', [])) for item in paris_results['data'] if 'frames' in item)
                        self.stdout.write(self.style.SUCCESS(f"✓ PARIS classification completed ({segments_count} segments found)"))
                    else:
                        self.stdout.write(self.style.WARNING("⚠ PARIS classification completed but no valid segments found"))
                        
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"✗ PARIS classification failed: {str(e)}"))

    def _choose_processor_interactively(self, processors_qs):
        """Interactively choose a processor from multiple options."""
        processors = list(processors_qs)

        self.stdout.write(self.style.ERROR(
            f"\nThe center has {len(processors)} endoscopy processors.\n"
            "Choose one for this import:\n"
        ))
        for idx, proc in enumerate(processors, 1):
            self.stdout.write(f"  [{idx}] {proc.name}")

        while True:
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
                self.stderr.write("❌ Please enter a number in the list above.\n")
                continue

            return processors[index]
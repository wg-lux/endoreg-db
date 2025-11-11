"""
Django management command to initialize the default AI model with metadata.
This command ensures that a default AI model exists with proper ModelMeta records.
"""

from django.core.management.base import BaseCommand
from django.core.management import call_command
from pathlib import Path
#FIXME
from endoreg_db.data.ai_model_meta import (
    default_multilabel_classification
)

from endoreg_db.models import AiModel
from endoreg_db.helpers.data_loader import (
    load_ai_model_label_data,
    load_ai_model_data,
)


class Command(BaseCommand):
    help = """
    Initialize the default AI model with metadata.
    This command ensures that the default segmentation model exists with proper ModelMeta records.
    """

    def add_arguments(self, parser):
        """
        Adds the --force command-line argument to control metadata recreation.
        
        The --force flag, when specified, forces the recreation of model metadata even if it already exists.
        """
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force recreation of the model metadata even if it exists',
        )

    def handle(self, *args, **options):
        """
        Initializes the default AI model and its metadata, creating or updating as needed.
        
        Loads required AI model label and model data, ensures the presence of a default AI model, and creates associated model metadata if it does not already exist or if the `--force` flag is specified. Generates a dummy weights file if necessary and invokes the metadata creation command. Verifies successful creation and outputs status messages throughout the process.
        """
        force = options.get('force', False)
        default_multilabel_classification()
        # First ensure the basic AI model data is loaded
        self.stdout.write("Loading AI model label data...")
        load_ai_model_label_data()
        
        self.stdout.write("Loading AI model data...")
        load_ai_model_data()

        # Check if default model exists
        default_model_name = "image_multilabel_classification_colonoscopy_default"
        
        try:
            ai_model = AiModel.objects.get(name=default_model_name)
            self.stdout.write(f"Found AI model: {ai_model.name}")
        except AiModel.DoesNotExist:
            self.stdout.write(
                self.style.ERROR(f"AI model '{default_model_name}' not found. Make sure AI model data is loaded.")
            )
            return

        # Check if model metadata exists
        existing_meta = ai_model.metadata_versions.first()
        if existing_meta and not force:
            self.stdout.write(
                self.style.SUCCESS(f"Model metadata already exists for {ai_model.name}. Use --force to recreate.")
            )
            return

        # Create default model metadata
        self.stdout.write("Creating default model metadata...")
        
        # Use a dummy weights file path for now - in production this should point to actual model weights
        dummy_weights_path = Path(__file__).parent.parent.parent / "assets" / "dummy_model.safetensors"
        
        # Create the dummy weights file if it doesn't exist
        dummy_weights_path.parent.mkdir(parents=True, exist_ok=True)
        if not dummy_weights_path.exists():
            dummy_weights_path.write_bytes(b"dummy weights content")
            self.stdout.write(f"Created dummy weights file at {dummy_weights_path}")

        try:
            # Create ModelMeta using the create_multilabel_model_meta command
            call_command(
                "create_multilabel_model_meta",
                "--model_path", str(dummy_weights_path),
                "--model_name", default_model_name,
                "--image_classification_labelset_name", "multilabel_classification_colonoscopy",
                "--activation_function_name", "sigmoid",
                "--model_meta_version", "1",
            )
            
            self.stdout.write(
                self.style.SUCCESS(f"Successfully created model metadata for {default_model_name}")
            )
            
            # Verify the model can be retrieved
            from endoreg_db.helpers.default_objects import get_latest_segmentation_model
            model_meta = get_latest_segmentation_model()
            self.stdout.write(
                self.style.SUCCESS(f"Verified: Model metadata can be retrieved: {model_meta}")
            )
            
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f"Failed to create model metadata: {e}")
            )
            raise